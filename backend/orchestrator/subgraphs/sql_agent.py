"""SQL을 생성·실행하고, 실패 시 self-correction(재시도)을 수행하는 SubGraph를 만든다."""

import logging
from collections.abc import Callable
from typing import Any, TypedDict

import psycopg
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from agents.sql.generator import generate_sql

logger = logging.getLogger(__name__)

# 원본 1회 + 재시도 2회 = 총 3회
MAX_ATTEMPTS = 3

# 접속/인프라 오류: 쿼리를 재생성해도 해결되지 않으므로 재시도 대상에서 제외한다.
_CONNECTION_EXCEPTIONS: tuple[type[Exception], ...] = (psycopg.OperationalError,)

# 실행/쿼리 결함 오류: LLM에 오류를 피드백해 쿼리를 재생성하면 해결될 수 있다.
_RETRYABLE_EXCEPTIONS: tuple[type[Exception], ...] = (
    psycopg.errors.SyntaxError,
    psycopg.errors.UndefinedColumn,
    psycopg.errors.UndefinedTable,
    psycopg.errors.UndefinedFunction,
    psycopg.errors.QueryCanceled,
)

_EMPTY_RESULT_ERROR = "EMPTY_RESULT"


class SQLAgentState(TypedDict):
    query: str
    entity: dict | None
    schema: str
    messages: list
    result: Any | None
    error: str | None
    attempt_count: int
    attempts: list[dict]
    empty_retried: bool
    retryable: bool


def _feedback_text(error: str | None) -> str | None:
    """state의 error 값을 LLM 재생성 프롬프트용 자연어 피드백으로 바꾼다."""
    if error is None:
        return None
    if error == _EMPTY_RESULT_ERROR:
        return (
            "이전 쿼리는 오류 없이 실행됐지만 결과가 없었습니다. "
            "조건(WHERE 절 등)이 지나치게 좁게 걸려 있지 않은지 다시 검토하세요."
        )
    return error


def make_sql_agent_subgraph(
    openai_client: Any,
    execute_sql: Callable[[str], Any],
) -> CompiledStateGraph:
    """SQL 생성 -> 실행 -> (실패 시) 재생성 재시도를 최대 MAX_ATTEMPTS회 반복하는
    SubGraph를 만든다. execute_sql 내부 구현(DB 접속, READ 전용 가드,
    rollback/commit 처리)은 이 함수의 책임이 아니며, 이미 올바르게 구현되어
    있다는 전제로 호출만 한다. 그래프 내부에서는 절대 raise하지 않고
    error 필드로만 실패를 전달한다."""

    def agent(state: SQLAgentState) -> dict:
        previous_message = state["messages"][-1] if state["messages"] else None
        sql = generate_sql(
            openai_client,
            query=state["query"],
            entity=state["entity"],
            schema_text=state["schema"],
            previous_query=previous_message["content"] if previous_message else None,
            previous_error=_feedback_text(state.get("error")),
        )
        return {
            "messages": [*state["messages"], {"role": "assistant", "content": sql}],
            "attempt_count": state.get("attempt_count", 0) + 1,
        }

    def tools(state: SQLAgentState) -> dict:
        sql = state["messages"][-1]["content"]
        attempts = state.get("attempts", [])

        try:
            result = execute_sql(sql)
        except _CONNECTION_EXCEPTIONS as exc:
            logger.error(
                "sql_agent: 접속 오류(재시도 대상 아님): %s", exc, exc_info=True
            )
            return {
                "error": str(exc),
                "result": None,
                "attempts": [*attempts, {"query": sql, "error": str(exc)}],
                "retryable": False,
            }
        except _RETRYABLE_EXCEPTIONS as exc:
            logger.warning("sql_agent: 실행 오류(재시도 대상): %s", exc, exc_info=True)
            return {
                "error": str(exc),
                "result": None,
                "attempts": [*attempts, {"query": sql, "error": str(exc)}],
                "retryable": True,
            }
        except Exception as exc:
            # 화이트리스트 밖 예외: 예상 못 한 버그가 재시도 뒤에 숨는 것을 막기 위한 안전망
            logger.error(
                "sql_agent: 미분류 예외(재시도 대상 아님, 안전망): %s",
                exc,
                exc_info=True,
            )
            return {
                "error": str(exc),
                "result": None,
                "attempts": [*attempts, {"query": sql, "error": str(exc)}],
                "retryable": False,
            }

        if not result and not state.get("empty_retried", False):
            logger.info("sql_agent: 결과 없음 - 1회 한정 재시도")
            return {
                "error": _EMPTY_RESULT_ERROR,
                "result": result,
                "attempts": [*attempts, {"query": sql, "error": _EMPTY_RESULT_ERROR}],
                "retryable": True,
                "empty_retried": True,
            }

        return {
            "result": result,
            "error": None,
            "attempts": [*attempts, {"query": sql, "error": None}],
            "retryable": False,
        }

    def should_retry(state: SQLAgentState) -> str:
        if state["error"] is None:
            return "done"
        if not state.get("retryable", False):
            return "done"
        if state["attempt_count"] >= MAX_ATTEMPTS:
            return "done"
        return "retry"

    graph = StateGraph(SQLAgentState)
    graph.add_node("agent", agent)
    graph.add_node("tools", tools)
    graph.add_edge(START, "agent")
    graph.add_edge("agent", "tools")
    graph.add_conditional_edges("tools", should_retry, {"retry": "agent", "done": END})
    return graph.compile()
