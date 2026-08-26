from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from agents.cypher.schema.loader import load_graph_schema
from agents.cypher.schema.models import GraphQueryPolicy, GraphSchema
from agents.cypher.schema.serializer import serialize_graph_schema
from agents.sql.schema.loader import load_sql_schema
from agents.sql.schema.serializer import serialize_sql_schema
from orchestrator.nodes.generate_answer import make_generate_answer_node
from orchestrator.nodes.resolve_entity import make_resolve_entity_node
from orchestrator.nodes.route_query import make_route_query_node
from orchestrator.state import OrchestratorState
from orchestrator.subgraphs.cypher_agent import make_cypher_agent_subgraph
from orchestrator.subgraphs.sql_agent import make_sql_agent_subgraph

_PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _load_schema_context() -> tuple[str, str, GraphSchema]:
    """SQL/Cypher 스키마를 프로젝트 YAML에서 읽는다."""
    sql_schema = load_sql_schema(_PROJECT_ROOT / "schema" / "sql_schema.yaml")
    cypher_schema = load_graph_schema(_PROJECT_ROOT / "schema" / "graph_schema.yaml")
    if cypher_schema.query_policy is None:
        raise ValueError("Graph schema requires BOM query policy metadata.")

    return (
        serialize_sql_schema(sql_schema),
        serialize_graph_schema(cypher_schema),
        cypher_schema,
    )


def _retry_agent_initial_state(
    query: str, entity: dict | list[dict] | None, schema_text: str
) -> dict:
    """sql_agent/cypher_agent SubGraph에 공통으로 넘기는 초기 상태를 만든다."""
    return {
        "query": query,
        "entity": entity,
        "schema": schema_text,
        "messages": [],
        "result": None,
        "error": None,
        "attempt_count": 0,
        "attempts": [],
        "empty_retried": False,
        "empty_reason": None,
    }


def _retry_agent_result_summary(result: dict) -> dict:
    """SubGraph 실행 결과에서 OrchestratorState에 노출할 필드만 뽑는다."""
    return {
        "result": result["result"],
        "error": result["error"],
        "attempts": result.get("attempts", []),
        "empty_reason": result.get("empty_reason"),
    }


def _execute_sql_stub(sql: str) -> Any:
    """self-correction 구현자가 실제 SQL 검증·실행 로직으로 교체할 자리."""
    raise NotImplementedError("SQL 실행/검증은 self-correction 구현에서 채운다.")


def _execute_cypher_stub(cypher: str) -> Any:
    """self-correction 구현자가 실제 Cypher 검증·실행 로직으로 교체할 자리."""
    raise NotImplementedError("Cypher 실행/검증은 self-correction 구현에서 채운다.")


def _make_sql_agent_node(
    openai_client: Any, sql_schema_text: str
) -> Callable[[OrchestratorState], dict]:
    """SQL Agent SubGraph를 감싸 OrchestratorState와 주고받는 노드를 만든다."""
    subgraph = make_sql_agent_subgraph(openai_client, execute_sql=_execute_sql_stub)

    def sql_agent(state: OrchestratorState) -> dict:
        if "sql" not in (state.get("tool_plan") or []):
            return {"sql_query": None, "sql_result": None}
        result = subgraph.invoke(
            _retry_agent_initial_state(
                state["query"], state.get("entity"), sql_schema_text
            )
        )
        return {
            "sql_query": result["messages"][-1]["content"],
            "sql_result": _retry_agent_result_summary(result),
        }

    return sql_agent


def _make_cypher_agent_node(
    openai_client: Any, cypher_schema_text: str, cypher_query_policy: GraphQueryPolicy
) -> Callable[[OrchestratorState], dict]:
    """Cypher Agent SubGraph를 감싸 OrchestratorState와 주고받는 노드를 만든다."""
    subgraph = make_cypher_agent_subgraph(
        openai_client,
        execute_cypher=_execute_cypher_stub,
        query_policy=cypher_query_policy,
    )

    def cypher_agent(state: OrchestratorState) -> dict:
        if "graph" not in (state.get("tool_plan") or []):
            return {"cypher_query": None, "graph_result": None}
        result = subgraph.invoke(
            _retry_agent_initial_state(
                state["query"], state.get("entity"), cypher_schema_text
            )
        )
        return {
            "cypher_query": result["messages"][-1]["content"],
            "graph_result": _retry_agent_result_summary(result),
        }

    return cypher_agent


# OpenAI/PostgreSQL 클라이언트를 주입받아 컴파일된 그래프를 반환
# START -> resolve_entity -> route_query -> sql_agent -> cypher_agent -> generate_answer -> END
def build_orchestrator_graph(
    openai_client: Any,
    postgres_connection: Any,
) -> CompiledStateGraph:
    sql_schema_text, cypher_schema_text, cypher_schema = _load_schema_context()
    cypher_query_policy = cypher_schema.query_policy
    assert cypher_query_policy is not None

    graph = StateGraph(OrchestratorState)
    # langgraph의 add_node 오버로드가 factory의 Callable 반환 타입을 추론하지
    # 못하므로, 런타임 시그니처를 검증한 노드만 Any로 좁혀 전달한다.
    graph.add_node(
        "resolve_entity",
        cast(
            Any,
            make_resolve_entity_node(openai_client, postgres_connection, cypher_schema),
        ),
    )
    graph.add_node("route_query", cast(Any, make_route_query_node(openai_client)))
    graph.add_node(
        "sql_agent",
        cast(Any, _make_sql_agent_node(openai_client, sql_schema_text)),
    )
    graph.add_node(
        "cypher_agent",
        cast(
            Any,
            _make_cypher_agent_node(
                openai_client, cypher_schema_text, cypher_query_policy
            ),
        ),
    )
    graph.add_node(
        "generate_answer",
        cast(Any, make_generate_answer_node()),
    )
    graph.add_edge(START, "resolve_entity")
    graph.add_edge("resolve_entity", "route_query")
    graph.add_edge("route_query", "sql_agent")
    graph.add_edge("sql_agent", "cypher_agent")
    graph.add_edge("cypher_agent", "generate_answer")
    graph.add_edge("generate_answer", END)
    return graph.compile()
