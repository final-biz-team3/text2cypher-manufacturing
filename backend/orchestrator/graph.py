from pathlib import Path
from typing import Any, cast

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from agents.cypher.schema.loader import load_graph_schema
from agents.cypher.schema.models import GraphSchema
from agents.cypher.schema.serializer import serialize_graph_schema
from agents.generator import DEFAULT_REASONING_EFFORT, ReasoningEffort
from agents.sql.schema.loader import load_sql_schema
from agents.sql.schema.models import SqlSchema
from agents.sql.schema.serializer import serialize_sql_schema
from orchestrator.execution.cypher_executor import execute_cypher
from orchestrator.execution.sql_executor import execute_sql
from orchestrator.nodes.classify_topic import make_classify_topic_node
from orchestrator.nodes.compose_results import make_compose_results_node
from orchestrator.nodes.execute_plan import make_execute_plan_node
from orchestrator.nodes.generate_answer import make_generate_answer_node
from orchestrator.nodes.guard_request import make_guard_request_node
from orchestrator.nodes.plan_outputs import make_plan_outputs_node
from orchestrator.nodes.resolve_entity import make_resolve_entity_node
from orchestrator.nodes.route_query import make_route_query_node
from orchestrator.output_catalog import build_output_catalog
from orchestrator.state import OrchestratorState
from orchestrator.subgraphs.cypher_agent import make_cypher_agent_subgraph
from orchestrator.subgraphs.sql_agent import make_sql_agent_subgraph

_PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _route_by_query_failure(state: OrchestratorState) -> str:
    """guard_request/classify_topic 공용: query_failure가 있으면 즉시 답변으로 뺀다."""
    return "blocked" if state.get("query_failure") is not None else "allowed"


def _load_schema_context() -> tuple[SqlSchema, str, GraphSchema, str]:
    """SQL/Cypher 스키마를 프로젝트 YAML에서 읽는다."""
    sql_schema = load_sql_schema(_PROJECT_ROOT / "schema" / "sql_schema.yaml")
    cypher_schema = load_graph_schema(_PROJECT_ROOT / "schema" / "graph_schema.yaml")
    if cypher_schema.query_policy is None:
        raise ValueError("Graph schema requires BOM query policy metadata.")

    return (
        sql_schema,
        serialize_sql_schema(sql_schema),
        cypher_schema,
        serialize_graph_schema(cypher_schema),
    )


# OpenAI 클라이언트/PostgreSQL 풀을 주입받아 컴파일된 그래프를 반환
# START -> guard_request -> classify_topic -> resolve_entity -> route_query
# -> plan_outputs -> execute_plan -> compose_results -> generate_answer -> END
def build_orchestrator_graph(
    openai_client: Any,
    pool: Any,
    *,
    reasoning_effort: ReasoningEffort = DEFAULT_REASONING_EFFORT,
) -> CompiledStateGraph:
    sql_schema, sql_schema_text, cypher_schema, cypher_schema_text = (
        _load_schema_context()
    )
    cypher_query_policy = cypher_schema.query_policy
    assert cypher_query_policy is not None
    output_catalog = build_output_catalog(sql_schema, cypher_schema)
    sql_agent = make_sql_agent_subgraph(
        openai_client,
        execute_sql=execute_sql,
        sql_schema=sql_schema,
        reasoning_effort=reasoning_effort,
    )
    cypher_agent = make_cypher_agent_subgraph(
        openai_client,
        execute_cypher=execute_cypher,
        query_policy=cypher_query_policy,
        graph_schema=cypher_schema,
        reasoning_effort=reasoning_effort,
    )

    graph = StateGraph(OrchestratorState)
    graph.add_node("guard_request", cast(Any, make_guard_request_node()))
    graph.add_node(
        "classify_topic",
        cast(
            Any,
            make_classify_topic_node(openai_client, reasoning_effort=reasoning_effort),
        ),
    )
    # LangGraph가 factory의 Callable 반환 타입을 추론하지 못해 cast한다
    # (async Callable의 런타임 시그니처는 StateGraph 노드 계약과 일치한다).
    graph.add_node(
        "resolve_entity",
        cast(Any, make_resolve_entity_node(openai_client, pool, cypher_schema)),
    )
    graph.add_node(
        "route_query",
        cast(
            Any,
            make_route_query_node(
                openai_client,
                reasoning_effort=reasoning_effort,
                shared_join_aliases=output_catalog.shared_join_aliases,
            ),
        ),
    )
    graph.add_node(
        "plan_outputs",
        cast(
            Any,
            make_plan_outputs_node(
                openai_client,
                output_catalog,
                reasoning_effort=reasoning_effort,
            ),
        ),
    )
    graph.add_node(
        "execute_plan",
        cast(
            Any,
            make_execute_plan_node(
                sql_agent=sql_agent,
                cypher_agent=cypher_agent,
                sql_schema_text=sql_schema_text,
                cypher_schema_text=cypher_schema_text,
            ),
        ),
    )
    graph.add_node(
        "compose_results",
        cast(Any, make_compose_results_node()),
    )
    graph.add_node(
        "generate_answer",
        cast(Any, make_generate_answer_node(openai_client)),
    )
    graph.add_edge(START, "guard_request")
    graph.add_conditional_edges(
        "guard_request",
        _route_by_query_failure,
        {"blocked": "generate_answer", "allowed": "classify_topic"},
    )
    graph.add_conditional_edges(
        "classify_topic",
        _route_by_query_failure,
        {"blocked": "generate_answer", "allowed": "resolve_entity"},
    )
    graph.add_edge("resolve_entity", "route_query")
    graph.add_edge("route_query", "plan_outputs")
    graph.add_edge("plan_outputs", "execute_plan")
    graph.add_edge("execute_plan", "compose_results")
    graph.add_edge("compose_results", "generate_answer")
    graph.add_edge("generate_answer", END)
    return graph.compile()
