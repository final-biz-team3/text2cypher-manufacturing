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
from orchestrator.guards.cypher_guard import make_cypher_guard
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


def _required_output_error(cypher: str, required_outputs: list[str]) -> str | None:
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
) -> CompiledStateGraph:
    """Cypher 생성 -> 실행 -> (실패 시) 재생성 재시도 SubGraph를 만든다.
    execute_cypher 내부 구현에 대한 전제는 make_retry_agent_subgraph 참고."""

    async def generate(
        state: RetryAgentState, previous_query: str | None, previous_error: str | None
    ) -> str:
        return await generate_cypher(
            openai_client,
            query=state["query"],
            entity=state["entity"],
            schema_text=state["schema"],
            query_policy=query_policy,
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
        query_contract_error=_required_output_error,
    )
