from pathlib import Path
from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from agents.cypher.schema.loader import load_graph_schema
from agents.cypher.schema.models import GraphQueryPolicy
from agents.cypher.schema.serializer import serialize_graph_schema
from agents.sql.schema.loader import load_sql_schema
from agents.sql.schema.serializer import serialize_sql_schema
from orchestrator.nodes.generate_queries import make_generate_queries_node
from orchestrator.nodes.resolve_entity import make_resolve_entity_node
from orchestrator.nodes.route_query import make_route_query_node
from orchestrator.state import OrchestratorState

_PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _load_schema_context() -> tuple[str, str, GraphQueryPolicy]:
    """SQL/Cypher 스키마와 BOM 쿼리 정책을 프로젝트 YAML에서 읽는다."""
    sql_schema = load_sql_schema(_PROJECT_ROOT / "schema" / "sql_schema.yaml")
    cypher_schema = load_graph_schema(_PROJECT_ROOT / "schema" / "graph_schema.yaml")
    if cypher_schema.query_policy is None:
        raise ValueError("Graph schema requires BOM query policy metadata.")

    return (
        serialize_sql_schema(sql_schema),
        serialize_graph_schema(cypher_schema),
        cypher_schema.query_policy,
    )


# OpenAI/PostgreSQL 클라이언트를 주입받아 컴파일된 그래프를 반환
# START -> resolve_entity -> route_query -> generate_queries -> END
def build_orchestrator_graph(
    openai_client: Any,
    postgres_connection: Any,
) -> CompiledStateGraph:
    sql_schema_text, cypher_schema_text, cypher_query_policy = _load_schema_context()

    graph = StateGraph(OrchestratorState)
    # mypy는 factory가 반환하는 `Callable[[OrchestratorState], dict]` 정적 타입을
    # add_node의 `_Node[NodeInputT] | ...` 오버로드 Union과 단일화하지 못해
    # call-overload 오류를 낸다(런타임 시그니처는 `_Node`와 정확히 일치). 이는
    # langgraph 1.2.11의 add_node 오버로드/mypy 2.3.0 조합에서 알려진 타입 추론
    # 한계이며, 인자를 top-level 함수로 직접 넘기면 재현되지 않는다.
    graph.add_node(
        "resolve_entity",
        make_resolve_entity_node(openai_client, postgres_connection),  # type: ignore[call-overload]
    )
    graph.add_node(
        "route_query", make_route_query_node(openai_client)  # type: ignore[call-overload]
    )
    graph.add_node(
        "generate_queries",
        make_generate_queries_node(
            openai_client,
            sql_schema_text=sql_schema_text,
            cypher_schema_text=cypher_schema_text,
            cypher_query_policy=cypher_query_policy,
        ),  # type: ignore[call-overload]
    )
    graph.add_edge(START, "resolve_entity")
    graph.add_edge("resolve_entity", "route_query")
    graph.add_edge("route_query", "generate_queries")
    graph.add_edge("generate_queries", END)
    return graph.compile()
