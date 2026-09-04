"""SQL을 생성·실행하고, 실패 시 self-correction(재시도)을 수행하는 SubGraph를 만든다."""

import logging
import re
from collections.abc import Awaitable, Callable
from typing import Any

import psycopg
import sqlparse
from langgraph.graph.state import CompiledStateGraph
from sqlparse import tokens as sql_tokens

from agents.generator import DEFAULT_REASONING_EFFORT, ReasoningEffort
from agents.sql.generator import generate_sql
from agents.sql.schema.models import SqlSchema
from orchestrator.guards.sql_guard import make_sql_guard
from orchestrator.query_failures import make_query_failure
from orchestrator.state import QueryFailure
from orchestrator.subgraphs.retry_agent import (
    RetryAgentState,
    make_retry_agent_subgraph,
)

logger = logging.getLogger(__name__)

# 접속/인프라 오류: 쿼리를 재생성해도 해결되지 않으므로 재시도 대상에서 제외한다.
_CONNECTION_EXCEPTIONS: tuple[type[Exception], ...] = (psycopg.OperationalError,)

# 실행/쿼리 결함 오류: LLM에 오류를 피드백해 쿼리를 재생성하면 해결될 수 있다.
# psycopg.errors.QueryCanceled는 OperationalError의 서브클래스지만
# make_retry_agent_subgraph가 이 튜플을 _CONNECTION_EXCEPTIONS보다 먼저
# 검사하므로 접속 오류로 오분류되지 않는다.
_RETRYABLE_EXCEPTIONS: tuple[type[Exception], ...] = (
    psycopg.errors.SyntaxError,
    psycopg.errors.UndefinedColumn,
    psycopg.errors.UndefinedTable,
    psycopg.errors.UndefinedFunction,
    psycopg.errors.QueryCanceled,
)

_EMPTY_RESULT_FEEDBACK = (
    "이전 쿼리는 오류 없이 실행됐지만 결과가 없었습니다. "
    "조건(WHERE 절 등)이 지나치게 좁게 걸려 있지 않은지 다시 검토하세요."
)

_REPAIR_INSTRUCTIONS = {
    "SQL_SYNTAX_ERROR": ("PostgreSQL 문법만 사용하고 잘못된 절을 최소 변경하세요.",),
    "SQL_UNDEFINED_COLUMN": (
        "physical schema에 선언된 컬럼만 사용하세요.",
        "필요하면 컬럼을 소유한 테이블 alias로 한정하세요.",
    ),
    "SQL_UNDEFINED_TABLE": ("physical schema에 선언된 테이블만 사용하세요.",),
    "SQL_UNDEFINED_FUNCTION": (
        "PostgreSQL과 제공된 스키마에서 유효한 함수만 사용하세요.",
    ),
    "SQL_TIMEOUT": (
        "질문의 필터를 제거하지 말고 불필요한 JOIN과 중복 계산을 줄이세요.",
    ),
    "SQL_OUTPUT_CONTRACT_FAILED": (
        "SELECT 목록 외의 FROM, JOIN, WHERE, GROUP BY, HAVING, ORDER BY, LIMIT은 변경하지 마세요.",
        "누락되거나 잘못된 output alias만 최소 수정하세요.",
        "required output을 정확한 대소문자의 quoted alias로 모두 반환하세요.",
    ),
    "SQL_EMPTY_RESULT": (
        "식별자와 질문의 필수 조건은 유지하세요.",
        "기존 조건을 전부 재작성하지 말고 불필요하게 좁은 조건 하나만 찾아 수정하세요.",
    ),
    "default": (
        "질문의 의미, 필터, input binding과 required output을 보존하세요.",
        "읽기 전용 SQL 하나만 반환하세요.",
    ),
}


def _classify_execution_error(exc: Exception) -> QueryFailure:
    if isinstance(exc, psycopg.errors.QueryCanceled):
        code, category = "SQL_TIMEOUT", "TIMEOUT"
        reason = "SQL 조회가 제한 시간 안에 완료되지 않았습니다."
    elif isinstance(exc, psycopg.errors.UndefinedColumn):
        code, category = "SQL_UNDEFINED_COLUMN", "QUERY_INVALID"
        reason = "현재 SQL 스키마에 존재하지 않는 컬럼을 참조했습니다."
    elif isinstance(exc, psycopg.errors.UndefinedTable):
        code, category = "SQL_UNDEFINED_TABLE", "QUERY_INVALID"
        reason = "현재 SQL 스키마에 존재하지 않는 테이블을 참조했습니다."
    elif isinstance(exc, psycopg.errors.UndefinedFunction):
        code, category = "SQL_UNDEFINED_FUNCTION", "QUERY_INVALID"
        reason = "현재 SQL 환경에서 사용할 수 없는 함수를 호출했습니다."
    else:
        code, category = "SQL_SYNTAX_ERROR", "QUERY_INVALID"
        reason = "생성된 SQL의 PostgreSQL 문법을 해석하지 못했습니다."
    return make_query_failure(
        code=code,
        stage="execution",
        category=category,
        kind="user_correctable",
        retryable=True,
        user_safe_reason=reason,
        suggested_action="조회 대상과 조건을 더 구체적으로 지정해 주세요.",
        failed_tool="sql",
    )


def _query_contract_error(sql: str, required_outputs: list[str]) -> str | None:
    if not required_outputs:
        return None
    statements = [item for item in sqlparse.parse(sql) if str(item).strip()]
    if len(statements) != 1:
        return "필수 alias를 검증할 SELECT 절이 없습니다."
    tokens = statements[0].tokens
    select_index = next(
        (
            index
            for index, token in enumerate(tokens)
            if token.ttype is sql_tokens.DML and token.normalized.upper() == "SELECT"
        ),
        None,
    )
    if select_index is None:
        return "필수 alias를 검증할 SELECT 절이 없습니다."
    projection_tokens = []
    for token in tokens[select_index + 1 :]:
        if token.ttype is sql_tokens.Keyword and token.normalized.upper() == "FROM":
            break
        projection_tokens.append(str(token))
    projection = "".join(projection_tokens)
    missing = []
    for alias in required_outputs:
        escaped = re.escape(alias)
        explicit = re.search(rf'(?i:\bAS\s+)(?:"{escaped}"|{escaped}\b)', projection)
        bare = re.search(
            rf'(?i:(?:^|,)\s*(?:DISTINCT\s+)?)(?:"{escaped}"|{escaped})\s*(?=,|$)',
            projection,
        )
        if explicit is None and bare is None:
            missing.append(alias)
    return (
        None if not missing else "SELECT에 필수 alias가 없습니다: " + ", ".join(missing)
    )


def make_sql_agent_subgraph(
    openai_client: Any,
    execute_sql: Callable[[str], Awaitable[Any]],
    sql_schema: SqlSchema,
    reasoning_effort: ReasoningEffort = DEFAULT_REASONING_EFFORT,
    semantic_context: str = "",
) -> CompiledStateGraph:
    """SQL 생성 -> 쿼리 가드 -> 실행 -> (실패 시) 재생성 재시도 SubGraph를 만든다.
    execute_sql 내부 구현에 대한 전제는 make_retry_agent_subgraph 참고."""

    async def generate(
        state: RetryAgentState, previous_query: str | None, previous_error: str | None
    ) -> str:
        return await generate_sql(
            openai_client,
            query=state["query"],
            source_scope=state.get("source_scope"),
            entity=state["entity"],
            schema_text=state["schema"],
            semantic_context=semantic_context,
            business_rules=state.get("business_rules", []),
            required_outputs=state.get("required_outputs", []),
            input_bindings=state.get("input_bindings"),
            previous_query=previous_query,
            previous_error=previous_error,
            reasoning_effort=reasoning_effort,
        )

    return make_retry_agent_subgraph(
        logger=logger,
        label="sql_agent",
        generate=generate,
        execute=execute_sql,
        connection_exceptions=_CONNECTION_EXCEPTIONS,
        retryable_exceptions=_RETRYABLE_EXCEPTIONS,
        empty_result_feedback=_EMPTY_RESULT_FEEDBACK,
        guard=make_sql_guard(sql_schema),
        query_contract_error=_query_contract_error,
        classify_execution_error=_classify_execution_error,
        repair_instructions=_REPAIR_INSTRUCTIONS,
        repair_engine_env="SQL_REPAIR_ENGINE",
    )
