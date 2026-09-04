"""Cypher를 생성·실행하고, 실패 시 self-correction(재시도)을 수행하는 SubGraph를 만든다."""

import logging
import re
from collections.abc import Awaitable, Callable
from typing import Any

from langgraph.graph.state import CompiledStateGraph
from neo4j.exceptions import (
    AuthError,
    ClientError,
    ConstraintError,
    CypherSyntaxError,
    CypherTypeError,
    ServiceUnavailable,
    SessionExpired,
)

from agents.cypher.generator import generate_cypher
from agents.cypher.schema.models import GraphQueryPolicy, GraphSchema
from agents.generator import DEFAULT_REASONING_EFFORT, ReasoningEffort
from orchestrator.cypher_contracts import (
    has_coupled_independent_bom_paths,
    has_relationship_list_used_as_path,
)
from orchestrator.guards.cypher_guard import make_cypher_guard
from orchestrator.query_failures import make_query_failure
from orchestrator.state import QueryFailure
from orchestrator.subgraphs.retry_agent import (
    RetryAgentState,
    make_retry_agent_subgraph,
)

logger = logging.getLogger(__name__)

# 접속/인프라 오류: 쿼리를 재생성해도 해결되지 않으므로 재시도 대상에서 제외한다.
# neo4j.exceptions.ClientError처럼 넓은 상위 클래스로 잡지 않는다
# (AuthError가 ClientError를 상속해서 오분류될 수 있음) — 구체적 서브클래스만 잡는다.
_CONNECTION_EXCEPTIONS: tuple[type[Exception], ...] = (
    ServiceUnavailable,
    SessionExpired,
    AuthError,
)

# Neo4j 쿼리 타임아웃은 전용 예외 서브클래스가 없고 ClientError + 이 code
# 값으로만 구분된다(실측 결과, md/2026-08-25-execute_sql_cypher-구현-고려사항-정리.md
# §2-6). 재시도 예산이 넘치는 쿼리를 재생성하면 해결될 수 있어 재시도
# 대상이다.
_TIMEOUT_ERROR_CODE = (
    "Neo.ClientError.Transaction.TransactionTimedOutClientConfiguration"
)


class _CypherQueryTimeoutError(Exception):
    """Neo4j ClientError 중 타임아웃 code만 이 타입으로 재포장해 재시도
    대상임을 표시한다. 원본 예외는 __cause__로 보존되고, retry_agent.py의
    failure()가 str(exc)로 메시지를 꺼낼 때도 원본 메시지를 그대로 쓴다."""

    def __init__(self, original: Exception) -> None:
        super().__init__(str(original))
        self.original = original


def _wrap_execute_cypher(
    execute_cypher: Callable[[str], Awaitable[Any]],
) -> Callable[[str], Awaitable[Any]]:
    """execute_cypher가 던진 ClientError 중 타임아웃 code만
    _CypherQueryTimeoutError로 재포장해 _RETRYABLE_EXCEPTIONS에 편입시킨다.
    AuthError도 ClientError의
    서브클래스라 이 except에 걸리지만, code가 타임아웃과 다르면 즉시 원본을
    그대로 다시 던지므로(재포장하지 않음) _CONNECTION_EXCEPTIONS 분류가
    깨지지 않는다."""

    async def wrapped(cypher: str) -> Any:
        try:
            return await execute_cypher(cypher)
        except ClientError as exc:
            if getattr(exc, "code", None) == _TIMEOUT_ERROR_CODE:
                raise _CypherQueryTimeoutError(exc) from exc
            raise

    return wrapped


# 실행/쿼리 결함 오류: LLM에 오류를 피드백해 쿼리를 재생성하면 해결될 수 있다.
_RETRYABLE_EXCEPTIONS: tuple[type[Exception], ...] = (
    CypherSyntaxError,
    CypherTypeError,
    ConstraintError,
    _CypherQueryTimeoutError,
)

_EMPTY_RESULT_FEEDBACK = (
    "이전 쿼리는 오류 없이 실행됐지만 결과가 없었습니다. "
    "탐색 조건(관계 방향, 라벨, 필터)이 지나치게 좁게 걸려 있지 않은지 "
    "다시 검토하세요."
)

_REPAIR_INSTRUCTIONS = {
    "CYPHER_SYNTAX_ERROR": ("Neo4j 5 문법으로 잘못된 절을 최소 변경하세요.",),
    "CYPHER_TYPE_ERROR": ("함수와 연산자가 받는 값의 Cypher 타입을 맞추세요.",),
    "CYPHER_TIMEOUT": (
        "질문의 필터를 제거하지 말고 탐색 깊이와 중복 경로를 줄이세요.",
    ),
    "CYPHER_OUTPUT_CONTRACT_FAILED": (
        "RETURN 목록 외의 MATCH, OPTIONAL MATCH, WHERE, WITH, 경로 깊이와 관계 방향은 변경하지 마세요.",
        "누락되거나 잘못된 output alias만 최소 수정하세요.",
        "required output을 정확한 대소문자의 alias로 모두 RETURN하세요.",
    ),
    "CYPHER_EMPTY_RESULT": (
        "식별자와 질문의 필수 조건은 유지하세요.",
        "경로 전체를 재작성하지 말고 불필요하게 좁은 조건 하나만 찾아 수정하세요.",
    ),
    "default": (
        "질문의 의미, 관계 방향, input binding과 required output을 보존하세요.",
        "허용된 label, relationship, property만 사용한 읽기 전용 Cypher를 반환하세요.",
    ),
}


def _classify_execution_error(exc: Exception) -> QueryFailure:
    if isinstance(exc, _CypherQueryTimeoutError):
        code, category = "CYPHER_TIMEOUT", "TIMEOUT"
        reason = "그래프 조회가 제한 시간 안에 완료되지 않았습니다."
    elif isinstance(exc, CypherTypeError):
        code, category = "CYPHER_TYPE_ERROR", "QUERY_INVALID"
        reason = "생성된 Cypher에서 값과 연산자의 타입이 맞지 않습니다."
    elif isinstance(exc, ConstraintError):
        code, category = "CYPHER_CONSTRAINT_ERROR", "QUERY_INVALID"
        reason = "생성된 Cypher가 그래프 제약 조건을 만족하지 못했습니다."
    else:
        code, category = "CYPHER_SYNTAX_ERROR", "QUERY_INVALID"
        reason = "생성된 Cypher의 Neo4j 문법을 해석하지 못했습니다."
    return make_query_failure(
        code=code,
        stage="execution",
        category=category,
        kind="user_correctable",
        retryable=True,
        user_safe_reason=reason,
        suggested_action="조회 대상과 관계 조건을 더 구체적으로 지정해 주세요.",
        failed_tool="graph",
    )


def _query_contract_error(cypher: str, required_outputs: list[str]) -> str | None:
    if has_relationship_list_used_as_path(cypher):
        return (
            "가변길이 관계 패턴의 대괄호 안 변수는 Path가 아니라 관계 List입니다. "
            "relationships(), nodes(), length()에 전달하려면 "
            "path = (start)-[:REL*]->(end) 형태로 전체 경로를 바인딩하고 path를 "
            "사용하세요. 관계 List가 필요하면 해당 변수를 함수 없이 직접 사용하세요."
        )
    if has_coupled_independent_bom_paths(cypher):
        return (
            "서로 다른 anchor의 독립적인 REQUIRES_COMPONENT 가변 경로가 같은 "
            "MATCH 절에서 공통 endpoint에 결합되었습니다. Neo4j 5의 relationship "
            "uniqueness가 경로 사이를 제약하지 않도록 각 anchor 경로를 별도의 "
            "MATCH 절에서 탐색하고 공통 destination 변수로 결합하세요. anchor별 "
            "minimumPathLength는 destination grain으로 먼저 집계하세요."
        )
    if not required_outputs:
        return None
    returns = list(re.finditer(r"(?i)\bRETURN\b", cypher))
    if not returns:
        return "필수 alias를 검증할 RETURN 절이 없습니다."
    projection = cypher[returns[-1].end() :]
    projection = re.split(
        r"(?i)\bORDER\s+BY\b|\bSKIP\b|\bLIMIT\b", projection, maxsplit=1
    )[0]
    missing: list[str] = []
    for alias in required_outputs:
        escaped = re.escape(alias)
        explicit = re.search(
            rf'(?i:\bAS\s+)(?:`{escaped}`|"{escaped}"|{escaped}\b)', projection
        )
        bare = re.search(
            rf'(?i:(?:^|,)\s*(?:DISTINCT\s+)?)(?:`{escaped}`|"{escaped}"|'
            rf"{escaped})\s*(?=,|$)",
            projection,
        )
        if explicit is None and bare is None:
            missing.append(alias)
    if not missing:
        return None
    return "RETURN에 필수 alias가 없습니다: " + ", ".join(missing)


def make_cypher_agent_subgraph(
    openai_client: Any,
    execute_cypher: Callable[[str], Awaitable[Any]],
    query_policy: GraphQueryPolicy,
    graph_schema: GraphSchema,
    reasoning_effort: ReasoningEffort = DEFAULT_REASONING_EFFORT,
    semantic_context: str = "",
) -> CompiledStateGraph:
    """Cypher 생성 -> 실행 -> (실패 시) 재생성 재시도 SubGraph를 만든다.
    execute_cypher 내부 구현에 대한 전제는 make_retry_agent_subgraph 참고."""

    async def generate(
        state: RetryAgentState, previous_query: str | None, previous_error: str | None
    ) -> str:
        return await generate_cypher(
            openai_client,
            query=state["query"],
            source_scope=state.get("source_scope"),
            entity=state["entity"],
            schema_text=state["schema"],
            query_policy=query_policy,
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
        label="cypher_agent",
        generate=generate,
        execute=_wrap_execute_cypher(execute_cypher),
        connection_exceptions=_CONNECTION_EXCEPTIONS,
        retryable_exceptions=_RETRYABLE_EXCEPTIONS,
        empty_result_feedback=_EMPTY_RESULT_FEEDBACK,
        guard=make_cypher_guard(graph_schema),
        query_contract_error=_query_contract_error,
        classify_execution_error=_classify_execution_error,
        repair_instructions=_REPAIR_INSTRUCTIONS,
        repair_engine_env="CYPHER_REPAIR_ENGINE",
    )
