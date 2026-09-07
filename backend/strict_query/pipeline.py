"""PR #47의 질의 처리만 실행한다. 자연어 답변은 호출하지 않는다."""

from pathlib import Path
from typing import Any, cast

from agents.generator import DEFAULT_REASONING_EFFORT, ReasoningEffort
from orchestrator.execution.cypher_executor import execute_cypher
from orchestrator.execution.sql_executor import execute_sql
from strict_query.snapshot.agents.cypher.schema.loader import load_graph_schema
from strict_query.snapshot.agents.cypher.schema.serializer import serialize_graph_schema
from strict_query.snapshot.agents.sql.schema.loader import load_sql_schema
from strict_query.snapshot.agents.sql.schema.serializer import serialize_sql_schema
from strict_query.snapshot.orchestrator.nodes.compose_results import (
    make_compose_results_node,
)
from strict_query.snapshot.orchestrator.nodes.execute_plan import make_execute_plan_node
from strict_query.snapshot.orchestrator.nodes.plan_outputs import make_plan_outputs_node
from strict_query.snapshot.orchestrator.nodes.resolve_entity import (
    make_resolve_entity_node,
)
from strict_query.snapshot.orchestrator.nodes.route_query import make_route_query_node
from strict_query.snapshot.orchestrator.output_catalog import build_output_catalog
from strict_query.snapshot.orchestrator.subgraphs.cypher_agent import (
    make_cypher_agent_subgraph,
)
from strict_query.snapshot.orchestrator.subgraphs.sql_agent import (
    make_sql_agent_subgraph,
)


def build_strict_query(
    openai_client: Any,
    pool: Any,
    *,
    reasoning_effort: ReasoningEffort = DEFAULT_REASONING_EFFORT,
) -> Any:
    schema_dir = Path(__file__).resolve().parent / "schema"
    sql_schema = load_sql_schema(schema_dir / "sql_schema.yaml")
    graph_schema = load_graph_schema(schema_dir / "graph_schema.yaml")
    assert graph_schema.query_policy is not None
    catalog = build_output_catalog(sql_schema, graph_schema)
    sql_agent = make_sql_agent_subgraph(
        openai_client,
        execute_sql=execute_sql,
        sql_schema=sql_schema,
        reasoning_effort=reasoning_effort,
    )
    graph_agent = make_cypher_agent_subgraph(
        openai_client,
        execute_cypher=execute_cypher,
        query_policy=graph_schema.query_policy,
        graph_schema=graph_schema,
        reasoning_effort=reasoning_effort,
    )
    nodes = [
        make_resolve_entity_node(openai_client, pool, graph_schema),
        make_route_query_node(
            openai_client,
            reasoning_effort=reasoning_effort,
            shared_join_aliases=catalog.shared_join_aliases,
        ),
        make_plan_outputs_node(
            openai_client, catalog, reasoning_effort=reasoning_effort
        ),
        make_execute_plan_node(
            sql_agent=sql_agent,
            cypher_agent=graph_agent,
            sql_schema_text=serialize_sql_schema(sql_schema),
            cypher_schema_text=serialize_graph_schema(graph_schema),
        ),
        make_compose_results_node(),
    ]

    async def run(query_input: dict[str, Any]) -> dict[str, Any]:
        state = dict(query_input)
        for node in nodes:
            state.update(await node(cast(Any, state)))
            if state.get("query_failure") is not None:
                break
        return state

    return run
