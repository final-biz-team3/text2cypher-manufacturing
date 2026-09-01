"""SQL을 생성·실행하고, 실패 시 self-correction(재시도)을 수행하는 SubGraph를 만든다."""

import logging
from collections.abc import Awaitable, Callable
from typing import Any

import psycopg
from langgraph.graph.state import CompiledStateGraph

from agents.generator import DEFAULT_REASONING_EFFORT, ReasoningEffort
from agents.sql.generator import generate_sql
from agents.sql.schema.models import SqlSchema
from orchestrator.guards.sql_guard import make_sql_guard
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
    )
