from collections.abc import Callable
from pathlib import Path
from typing import Any

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
            {
                "query": state["query"],
                "entity": state.get("entity"),
                "schema": sql_schema_text,
                "messages": [],
                "result": None,
                "error": None,
            }
        )
        return {
            "sql_query": result["messages"][-1]["content"],
            "sql_result": {"result": result["result"], "error": result["error"]},
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
            {
                "query": state["query"],
                "entity": state.get("entity"),
                "schema": cypher_schema_text,
                "messages": [],
                "result": None,
                "error": None,
            }
        )
        return {
            "cypher_query": result["messages"][-1]["content"],
            "graph_result": {"result": result["result"], "error": result["error"]},
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
    # mypy는 factory가 반환하는 `Callable[[OrchestratorState], dict]` 정적 타입을
    # add_node의 `_Node[NodeInputT] | ...` 오버로드 Union과 단일화하지 못해
    # arg-type 오류를 낸다(런타임 시그니처는 `_Node`와 정확히 일치). 이는
    # langgraph 1.2.11의 add_node 오버로드/mypy 2.3.0 조합에서 알려진 타입 추론
    # 한계이며, 인자를 top-level 함수로 직접 넘기면 재현되지 않는다.
    graph.add_node(
        "resolve_entity",
        make_resolve_entity_node(
            openai_client, postgres_connection, cypher_schema
        ),  # type: ignore[arg-type]
    )
    graph.add_node(
        "route_query", make_route_query_node(openai_client)  # type: ignore[arg-type]
    )
    graph.add_node(
        "sql_agent",
        _make_sql_agent_node(openai_client, sql_schema_text),  # type: ignore[arg-type]
    )
    graph.add_node(
        "cypher_agent",
        _make_cypher_agent_node(
            openai_client, cypher_schema_text, cypher_query_policy
        ),  # type: ignore[arg-type]
    )
    graph.add_node(
        "generate_answer",
        make_generate_answer_node(),  # type: ignore[arg-type]
    )
    graph.add_edge(START, "resolve_entity")
    graph.add_edge("resolve_entity", "route_query")
    graph.add_edge("route_query", "sql_agent")
    graph.add_edge("sql_agent", "cypher_agent")
    graph.add_edge("cypher_agent", "generate_answer")
    graph.add_edge("generate_answer", END)
    return graph.compile()
