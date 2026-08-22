"""라우팅 결과에 따라 SQL과 Cypher 쿼리를 생성한다."""

from collections.abc import Callable
from typing import Any

from agents.cypher.generator import generate_cypher
from agents.cypher.schema.models import GraphQueryPolicy
from agents.sql.generator import generate_sql
from orchestrator.state import OrchestratorState

_SUPPORTED_TOOLS = {"sql", "graph"}


def make_generate_queries_node(
    openai_client: Any,
    *,
    sql_schema_text: str,
    cypher_schema_text: str,
    cypher_query_policy: GraphQueryPolicy,
) -> Callable[[OrchestratorState], dict[str, str | None]]:
    """스키마를 재사용하며 필요한 언어의 쿼리만 생성하는 노드를 만든다."""

    def generate_queries(state: OrchestratorState) -> dict[str, str | None]:
        tool_plan = state.get("tool_plan")
        if not tool_plan:
            raise ValueError("Query generation requires a non-empty tool plan.")

        unsupported_tools = set(tool_plan) - _SUPPORTED_TOOLS
        if unsupported_tools:
            names = ", ".join(sorted(unsupported_tools))
            raise ValueError(f"Unsupported query generation tools: {names}.")

        generated: dict[str, str | None] = {
            "sql_query": None,
            "cypher_query": None,
        }
        if "sql" in tool_plan:
            generated["sql_query"] = generate_sql(
                openai_client,
                query=state["query"],
                entity=state.get("entity"),
                schema_text=sql_schema_text,
            )

        if "graph" in tool_plan:
            generated["cypher_query"] = generate_cypher(
                openai_client,
                query=state["query"],
                entity=state.get("entity"),
                schema_text=cypher_schema_text,
                query_policy=cypher_query_policy,
            )

        return generated

    return generate_queries
