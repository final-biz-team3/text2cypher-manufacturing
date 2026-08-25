"""Cypher를 생성·실행하고, 실패 시 self-correction(재시도)을 수행하는 SubGraph를 만든다."""

import logging
from collections.abc import Callable
from typing import Any

from langgraph.graph.state import CompiledStateGraph
from neo4j.exceptions import (
    AuthError,
    ConstraintError,
    CypherSyntaxError,
    CypherTypeError,
    ServiceUnavailable,
    SessionExpired,
)

from agents.cypher.generator import generate_cypher
from agents.cypher.schema.models import GraphQueryPolicy
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

# 실행/쿼리 결함 오류: LLM에 오류를 피드백해 쿼리를 재생성하면 해결될 수 있다.
_RETRYABLE_EXCEPTIONS: tuple[type[Exception], ...] = (
    CypherSyntaxError,
    CypherTypeError,
    ConstraintError,
)

_EMPTY_RESULT_FEEDBACK = (
    "이전 쿼리는 오류 없이 실행됐지만 결과가 없었습니다. "
    "탐색 조건(관계 방향, 라벨, 필터)이 지나치게 좁게 걸려 있지 않은지 "
    "다시 검토하세요."
)


def make_cypher_agent_subgraph(
    openai_client: Any,
    execute_cypher: Callable[[str], Any],
    query_policy: GraphQueryPolicy,
) -> CompiledStateGraph:
    """Cypher 생성 -> 실행 -> (실패 시) 재생성 재시도 SubGraph를 만든다.
    execute_cypher 내부 구현에 대한 전제는 make_retry_agent_subgraph 참고."""

    def generate(
        state: RetryAgentState, previous_query: str | None, previous_error: str | None
    ) -> str:
        return generate_cypher(
            openai_client,
            query=state["query"],
            entity=state["entity"],
            schema_text=state["schema"],
            query_policy=query_policy,
            previous_query=previous_query,
            previous_error=previous_error,
        )

    return make_retry_agent_subgraph(
        logger=logger,
        label="cypher_agent",
        generate=generate,
        execute=execute_cypher,
        connection_exceptions=_CONNECTION_EXCEPTIONS,
        retryable_exceptions=_RETRYABLE_EXCEPTIONS,
        empty_result_feedback=_EMPTY_RESULT_FEEDBACK,
    )
