"""쿼리 생성 -> 실행 -> (실패 시) 재생성 재시도 SubGraph의 공통 배선.

sql_agent.py/cypher_agent.py는 완전히 대칭 구조라(생성 함수, 실행 콜백, DB별
예외 화이트리스트, 빈 결과 피드백 문구만 다름) 여기서 재시도 상태 머신
(agent -> tools -> should_retry 사이클, 예외 3분류, 빈 결과 1회 재시도)을
한 곳에 모아두고 각 모듈은 설정값만 주입한다."""

import logging
from collections.abc import Awaitable, Callable
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

# 원본 1회 + 재시도 2회 = 총 3회
MAX_ATTEMPTS = 3

EMPTY_RESULT_ERROR = "EMPTY_RESULT"

# 빈 결과가 최종 수용될 때, 이전 시도 이력에 실행 오류(EMPTY_RESULT가 아닌 진짜
# 쿼리 결함)가 있었는지로 원인을 구분한다.
NO_DATA = "NO_DATA"
INCONCLUSIVE = "INCONCLUSIVE"


class RetryAgentState(TypedDict):
    query: str
    entity: dict | list[dict] | None
    schema: str
    messages: list
    result: Any | None
    error: str | None
    attempt_count: int
    attempts: list[dict]
    empty_retried: bool
    retryable: bool
    empty_reason: str | None


def _classify_empty_result(prior_attempts: list[dict]) -> str:
    """이번 빈 결과 이전 시도 중 실행 오류가 하나라도 있었으면, 그 오류가
    정말 고쳐졌다는 보장이 없으므로 INCONCLUSIVE로 본다. 전부 깨끗하게
    실행됐는데 결과만 없었다면 NO_DATA(실제로 데이터가 없음)로 본다."""
    has_real_error = any(
        attempt["error"] not in (None, EMPTY_RESULT_ERROR) for attempt in prior_attempts
    )
    return INCONCLUSIVE if has_real_error else NO_DATA


def make_retry_agent_subgraph(
    *,
    logger: logging.Logger,
    label: str,
    generate: Callable[[RetryAgentState, str | None, str | None], Awaitable[str]],
    execute: Callable[[str], Awaitable[Any]],
    connection_exceptions: tuple[type[Exception], ...],
    retryable_exceptions: tuple[type[Exception], ...],
    empty_result_feedback: str,
) -> CompiledStateGraph:
    """query/entity/schema를 state에서 읽어 쿼리 문자열을 생성하는 generate와,
    쿼리 문자열을 실행하는 execute를 주입받아 재시도 SubGraph를 만든다.
    connection_exceptions는 retryable_exceptions보다 반드시 나중에 검사한다
    (예: psycopg.errors.QueryCanceled는 psycopg.OperationalError의 서브클래스라
    순서를 바꾸면 재시도 대상인데도 접속 오류로 오분류된다). execute 내부 구현
    (DB 접속, READ 전용 가드, rollback/commit)은 이 함수의 책임이 아니며 이미
    올바르게 구현되어 있다는 전제로 호출만 한다. 그래프 내부에서는 절대
    raise하지 않고 error 필드로만 실패를 전달한다."""

    def feedback_text(error: str | None) -> str | None:
        if error is None:
            return None
        if error == EMPTY_RESULT_ERROR:
            return empty_result_feedback
        return error

    async def agent(state: RetryAgentState) -> dict:
        previous_message = state["messages"][-1] if state["messages"] else None
        query_text = await generate(
            state,
            previous_message["content"] if previous_message else None,
            feedback_text(state.get("error")),
        )
        return {
            "messages": [
                *state["messages"],
                {"role": "assistant", "content": query_text},
            ],
            "attempt_count": state.get("attempt_count", 0) + 1,
            # RetryAgentState.retryable은 필수 필드지만 그래프 진입 시점의
            # 초기 상태(graph.py)에는 없을 수 있다. agent는 tools보다 항상
            # 먼저 실행되므로 여기서 기본값을 채워 should_retry가 첫 턴부터
            # state["retryable"]을 안전하게 읽을 수 있게 한다.
            "retryable": state.get("retryable", False),
        }

    async def tools(state: RetryAgentState) -> dict:
        query_text = state["messages"][-1]["content"]
        attempts = state.get("attempts", [])

        def failure(exc: Exception, *, retryable: bool) -> dict:
            return {
                "error": str(exc),
                "result": None,
                "attempts": [*attempts, {"query": query_text, "error": str(exc)}],
                "retryable": retryable,
            }

        try:
            result = await execute(query_text)
        except retryable_exceptions as exc:
            logger.warning("%s: 실행 오류(재시도 대상): %s", label, exc, exc_info=True)
            return failure(exc, retryable=True)
        except connection_exceptions as exc:
            logger.error(
                "%s: 접속 오류(재시도 대상 아님): %s", label, exc, exc_info=True
            )
            return failure(exc, retryable=False)
        except Exception as exc:
            # 화이트리스트 밖 예외: 예상 못 한 버그가 재시도 뒤에 숨는 것을 막기 위한 안전망
            logger.error(
                "%s: 미분류 예외(재시도 대상 아님, 안전망): %s",
                label,
                exc,
                exc_info=True,
            )
            return failure(exc, retryable=False)

        if not result:
            new_attempts = [
                *attempts,
                {"query": query_text, "error": EMPTY_RESULT_ERROR},
            ]
            can_retry_empty = (
                not state.get("empty_retried", False)
                and state.get("attempt_count", 0) < MAX_ATTEMPTS
            )
            if can_retry_empty:
                logger.info("%s: 결과 없음 - 1회 한정 재시도", label)
                return {
                    "error": EMPTY_RESULT_ERROR,
                    "result": result,
                    "attempts": new_attempts,
                    "retryable": True,
                    "empty_retried": True,
                }
            reason = _classify_empty_result(attempts)
            logger.info("%s: 결과 없음으로 최종 수용 (%s)", label, reason)
            return {
                "result": result,
                "error": None,
                "empty_reason": reason,
                "attempts": new_attempts,
                "retryable": False,
            }

        return {
            "result": result,
            "error": None,
            "empty_reason": None,
            "attempts": [*attempts, {"query": query_text, "error": None}],
            "retryable": False,
        }

    def should_retry(state: RetryAgentState) -> str:
        if state["error"] is None:
            return "done"
        if not state.get("retryable", False):
            return "done"
        if state["attempt_count"] >= MAX_ATTEMPTS:
            return "done"
        return "retry"

    graph = StateGraph(RetryAgentState)
    graph.add_node("agent", agent)
    graph.add_node("tools", tools)
    graph.add_edge(START, "agent")
    graph.add_edge("agent", "tools")
    graph.add_conditional_edges("tools", should_retry, {"retry": "agent", "done": END})
    return graph.compile()
