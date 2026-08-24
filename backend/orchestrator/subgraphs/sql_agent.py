"""SQL을 한 번 생성하고 한 번 실행을 시도하는 뼈대 SubGraph를 만든다."""

from collections.abc import Callable
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from agents.sql.generator import generate_sql


class SQLAgentState(TypedDict):
    query: str
    entity: dict | None
    schema: str
    messages: list
    result: Any | None
    error: str | None


def make_sql_agent_subgraph(
    openai_client: Any,
    execute_sql: Callable[[str], Any],
) -> CompiledStateGraph:
    """SQL 생성 1회·실행 시도 1회 뼈대를 만든다. execute_sql은 self-correction
    구현자가 실제 검증·실행 로직으로 교체하는 자리다. 재시도는 이 뼈대에 없다."""

    def agent(state: SQLAgentState) -> dict:
        sql = generate_sql(
            openai_client,
            query=state["query"],
            entity=state["entity"],
            schema_text=state["schema"],
        )
        return {"messages": [*state["messages"], {"role": "assistant", "content": sql}]}

    def tools(state: SQLAgentState) -> dict:
        sql = state["messages"][-1]["content"]
        try:
            result = execute_sql(sql)
        except Exception as exc:
            return {"error": str(exc), "result": None}
        return {"result": result, "error": None}

    graph = StateGraph(SQLAgentState)
    graph.add_node("agent", agent)
    graph.add_node("tools", tools)
    graph.add_edge(START, "agent")
    graph.add_edge("agent", "tools")
    graph.add_edge("tools", END)
    return graph.compile()
