"""Output planner 테스트의 반복 배선만 제공하는 test-owned support."""

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, cast

from agents.cypher.schema.loader import load_graph_schema
from agents.sql.schema.loader import load_sql_schema
from orchestrator.nodes.plan_outputs import _complete_outputs, make_plan_outputs_node
from orchestrator.output_catalog import OutputCatalog, build_output_catalog
from orchestrator.state import OrchestratorState
from tests.mocks.openai import MockOpenAIClient, make_content_response

PROJECT_ROOT = Path(__file__).resolve().parents[3]

AGGREGATE_STOCK_OUTPUTS = ["productId", "productName", "actualStock"]
COMMON_COMPONENT_OUTPUTS = [
    "finishedProductIdA",
    "finishedProductIdB",
    "componentId",
    "componentName",
    "minDepthA",
    "minDepthB",
]
LOCATION_ROW_OUTPUTS = [
    "productId",
    "productName",
    "locationId",
    "locationName",
    "shelf",
    "bin",
    "quantity",
]


@lru_cache(maxsize=1)
def output_catalog() -> OutputCatalog:
    """검증된 schema catalog를 테스트 프로세스에서 한 번만 생성한다."""
    return build_output_catalog(
        load_sql_schema(PROJECT_ROOT / "schema" / "sql_schema.yaml"),
        load_graph_schema(PROJECT_ROOT / "schema" / "graph_schema.yaml"),
    )


def complete_outputs(
    *,
    query: str,
    selected_outputs: list[str],
    tool: str = "sql",
    entity: object = None,
    confirmed_entity: object = None,
    route_question: str | None = None,
    join_keys: list[str] | None = None,
) -> list[str]:
    """LLM·node 배선과 분리해 deterministic output policy만 검증한다."""
    return _complete_outputs(
        selected_outputs,
        tool=tool,
        entity=entity,
        confirmed_entity=confirmed_entity,
        question=route_question or query,
        original_question=query,
        join_keys=join_keys or [],
        catalog=output_catalog(),
    )


async def plan_single_subquery(
    *,
    query: str,
    selected_outputs: list[str],
    tool: str = "sql",
    entity: object = None,
    confirmed_entity: object = None,
    route_question: str | None = None,
    subquery_id: str | None = None,
    join_keys: list[str] | None = None,
) -> tuple[dict[str, Any], MockOpenAIClient]:
    """단일 subquery의 output 보정 계약을 최소 state로 실행한다."""
    client = MockOpenAIClient(
        make_content_response(
            json.dumps(
                {"requiredOutputs": selected_outputs},
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
    )
    node = make_plan_outputs_node(client, output_catalog())
    resolved_id = subquery_id or f"{tool}_query"
    subquery: dict[str, Any] = {
        "id": resolved_id,
        "tool": tool,
        "question": route_question or query,
        "dependsOn": [],
        "joinKeys": join_keys or [],
    }
    state: dict[str, Any] = {
        "query": query,
        "entity": entity,
        "tool_plan": [tool],
        "routeDraft": {
            "tool_plan": [tool],
            "subqueries": [subquery],
        },
        "resultTransform": None,
    }
    if confirmed_entity is not None:
        state["confirmed_entity"] = confirmed_entity
    return await node(cast(OrchestratorState, state)), client
