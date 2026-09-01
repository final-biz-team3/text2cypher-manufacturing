"""Lossless output planning and structural finalization contracts."""

import asyncio
from functools import lru_cache
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from agents.cypher.schema.loader import load_graph_schema
from agents.sql.schema.loader import load_sql_schema
from orchestrator.nodes.plan_outputs import (
    finalize_required_outputs,
    make_plan_outputs_node,
    output_plan_json_schema,
)
from orchestrator.output_catalog import OutputCatalog, build_output_catalog
from orchestrator.planning import BomShortageTransform, RouteSubquery
from orchestrator.state import OrchestratorState
from tests.mocks.openai import MockOpenAIClient, make_content_response

PROJECT_ROOT = Path(__file__).resolve().parents[3]


@lru_cache(maxsize=1)
def _catalog() -> OutputCatalog:
    return build_output_catalog(
        load_sql_schema(PROJECT_ROOT / "schema" / "sql_schema.yaml"),
        load_graph_schema(PROJECT_ROOT / "schema" / "graph_schema.yaml"),
    )


def _state(
    subqueries: list[RouteSubquery],
    *,
    query: str = "synthetic question",
    entity: dict | list[dict] | None = None,
    transform: BomShortageTransform | None = None,
) -> OrchestratorState:
    return {
        "query": query,
        "entity": entity,
        "tool_plan": [item["tool"] for item in subqueries],
        "routeDraft": {
            "tool_plan": [item["tool"] for item in subqueries],
            "subqueries": subqueries,
        },
        "resultTransform": transform,
    }


def test_catalog_separates_physical_role_and_concept_aliases() -> None:
    catalog = _catalog()

    assert catalog.by_tool["sql"]["productId"].kind == "physical"
    assert catalog.by_tool["graph"]["componentId"].kind == "role"
    assert catalog.by_tool["sql"]["actualStock"].kind == "aggregate"
    assert catalog.by_tool["graph"]["pathProductIds"].kind == "path"
    assert "componentId" in catalog.shared_join_aliases
    assert "categoryId" not in catalog.shared_join_aliases


def test_output_schema_uses_provider_supported_shape() -> None:
    schema = output_plan_json_schema(_catalog(), "sql")

    assert "uniqueItems" not in schema["properties"]["requiredOutputs"]


def test_finalizer_preserves_selection_order_and_adds_only_structure() -> None:
    assert finalize_required_outputs(
        ["sharedComponentCount", "supplierIdA"],
        ["componentId"],
        ["quantityPerAssembly"],
        _catalog(),
        tool="graph",
    ) == [
        "sharedComponentCount",
        "supplierIdA",
        "componentId",
        "quantityPerAssembly",
    ]


@pytest.mark.parametrize(
    ("selected", "join_keys", "outgoing", "message"),
    [
        (["productId", "productId"], [], [], "duplicate"),
        (["pathProductIds"], [], [], "does not own"),
        (["productId"], ["pathProductIds"], [], "does not own"),
    ],
)
def test_finalizer_rejects_duplicates_and_source_violations(
    selected: list[str],
    join_keys: list[str],
    outgoing: list[str],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        finalize_required_outputs(
            selected,
            join_keys,
            outgoing,
            _catalog(),
            tool="sql",
        )


@pytest.mark.parametrize(
    ("query", "entity", "selected"),
    [
        (
            "두 공급처가 함께 취급하는 항목의 수와 쌍을 보여줘",
            None,
            ["supplierIdA", "supplierIdB", "sharedComponentCount"],
        ),
        (
            "조립 링크의 한 단위당 투입량도 표시해줘",
            None,
            ["componentId", "quantityPerAssembly"],
        ),
        (
            "종료일이 비어 있는 행에서 종료일도 표시해줘",
            None,
            ["productId", "sellEndDate"],
        ),
        (
            "두 조립품의 공통 항목을 scalar로만 요약해줘",
            [
                {"productId": 9101, "productName": "One"},
                {"productId": 9102, "productName": "One Extended"},
            ],
            ["componentId", "sharedComponentCount"],
        ),
        (
            "두 조립품의 공통 항목과 경로를 표시해줘",
            None,
            ["componentId", "pathProductIds"],
        ),
    ],
)
async def test_model_output_is_authoritative_and_question_is_unchanged(
    query: str,
    entity: Any,
    selected: list[str],
) -> None:
    tool = (
        "sql"
        if all(alias in _catalog().by_tool["sql"] for alias in selected)
        else "graph"
    )
    client = MockOpenAIClient(
        make_content_response(
            '{"requiredOutputs":[' + ",".join(f'"{alias}"' for alias in selected) + "]}"
        )
    )
    subquery: RouteSubquery = {
        "id": f"{tool}_synthetic",
        "tool": tool,
        "question": query,
        "dependsOn": [],
        "joinKeys": [],
    }

    result = await make_plan_outputs_node(client, _catalog())(
        _state([subquery], query=query, entity=entity)
    )

    assert result["subqueries"][0]["requiredOutputs"] == selected
    assert result["subqueries"][0]["question"] == query


async def test_entity_names_with_domain_words_do_not_rewrite_the_subquery() -> None:
    question = "warehouse 공급 폐기 location이라는 제품의 color를 표시해줘"
    entity = {
        "productId": 9201,
        "productName": "warehouse 공급 폐기 location",
    }
    client = MockOpenAIClient(
        make_content_response('{"requiredOutputs":["productId","color"]}')
    )
    subquery: RouteSubquery = {
        "id": "sql_color",
        "tool": "sql",
        "question": question,
        "dependsOn": [],
        "joinKeys": [],
    }

    result = await make_plan_outputs_node(client, _catalog())(
        _state([subquery], query=question, entity=entity)
    )

    assert result["subqueries"][0]["question"] == question
    assert result["subqueries"][0]["requiredOutputs"] == ["productId", "color"]


async def test_downstream_non_identity_binding_is_added_to_producer_outputs() -> None:
    client = MockOpenAIClient(
        make_content_response('{"requiredOutputs":["componentName"]}'),
        make_content_response('{"requiredOutputs":["actualStock"]}'),
    )
    subqueries: list[RouteSubquery] = [
        {
            "id": "graph_components",
            "tool": "graph",
            "question": "component rows",
            "dependsOn": [],
            "joinKeys": [],
        },
        {
            "id": "sql_measures",
            "tool": "sql",
            "question": "measure rows",
            "dependsOn": ["graph_components"],
            "joinKeys": [],
            "inputBindings": {"quantities": "graph_components.quantityPerAssembly"},
        },
    ]

    result = await make_plan_outputs_node(client, _catalog())(_state(subqueries))

    assert result["subqueries"][0]["requiredOutputs"] == [
        "componentName",
        "quantityPerAssembly",
    ]
    assert result["subqueries"][0]["joinKeys"] == []
    assert result["subqueries"][1]["joinKeys"] == []


async def test_independent_output_selection_calls_run_concurrently() -> None:
    both_started = asyncio.Event()
    started: list[str] = []

    class _ConcurrentCompletions:
        async def create(self, **kwargs):
            content = kwargs["messages"][-1]["content"]
            tool = "sql" if "source: sql" in content else "graph"
            started.append(tool)
            if len(started) == 2:
                both_started.set()
            await asyncio.wait_for(both_started.wait(), timeout=0.2)
            output = "productCount" if tool == "sql" else "minDepth"
            return make_content_response(f'{{"requiredOutputs":["{output}"]}}')

    client = SimpleNamespace(chat=SimpleNamespace(completions=_ConcurrentCompletions()))
    subqueries: list[RouteSubquery] = [
        {
            "id": "sql_count",
            "tool": "sql",
            "question": "count",
            "dependsOn": [],
            "joinKeys": [],
        },
        {
            "id": "graph_depth",
            "tool": "graph",
            "question": "depth",
            "dependsOn": [],
            "joinKeys": [],
        },
    ]

    result = await make_plan_outputs_node(client, _catalog())(_state(subqueries))

    assert started == ["sql", "graph"]
    assert [item["requiredOutputs"] for item in result["subqueries"]] == [
        ["productCount"],
        ["minDepth"],
    ]


async def test_structural_retry_does_not_add_an_extra_planning_stage() -> None:
    client = MockOpenAIClient(
        make_content_response('{"requiredOutputs":["pathProductIds"]}'),
        make_content_response('{"requiredOutputs":["productCount"]}'),
    )
    subquery: RouteSubquery = {
        "id": "sql_count",
        "tool": "sql",
        "question": "count",
        "dependsOn": [],
        "joinKeys": [],
    }

    result = await make_plan_outputs_node(client, _catalog())(_state([subquery]))

    assert result["subqueries"][0]["requiredOutputs"] == ["productCount"]
    assert len(client.calls) == 2


async def test_formal_transform_is_model_free_and_keeps_question_text() -> None:
    client = MockOpenAIClient()
    question = "formal shortage calculation"
    graph_id = "graph_bom"
    subqueries: list[RouteSubquery] = [
        {
            "id": graph_id,
            "tool": "graph",
            "question": question,
            "dependsOn": [],
            "joinKeys": ["componentId"],
        },
        {
            "id": "sql_stock",
            "tool": "sql",
            "question": question,
            "dependsOn": [graph_id],
            "joinKeys": ["componentId"],
            "inputBindings": {"componentIds": f"{graph_id}.componentId"},
        },
    ]

    result = await make_plan_outputs_node(client, _catalog())(
        _state(
            subqueries,
            query=question,
            transform={"type": "bom_shortage_v1", "productionQty": 3},
        )
    )

    transform_spec = _catalog().transform("bom_shortage_v1")
    assert client.calls == []
    assert [item["requiredOutputs"] for item in result["subqueries"]] == [
        list(transform_spec.required_outputs["graph"]),
        list(transform_spec.required_outputs["sql"]),
    ]
    assert all(item["question"] == question for item in result["subqueries"])
    assert all(item.get("generatorRules") for item in result["subqueries"])
