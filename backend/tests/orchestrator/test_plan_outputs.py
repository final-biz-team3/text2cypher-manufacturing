"""Lossless output planning and structural finalization contracts."""

import asyncio
from functools import lru_cache
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Literal

import pytest

from agents.cypher.schema.loader import load_graph_schema
from agents.sql.schema.loader import load_sql_schema
from orchestrator.nodes.plan_outputs import (
    PlannedResultEntity,
    SemanticOutputPlan,
    compile_semantic_output_plan,
    finalize_required_outputs,
    make_plan_outputs_node,
    output_plan_json_schema,
)
from orchestrator.output_catalog import OutputCatalog, build_output_catalog
from orchestrator.planning import BomShortageTransform, RouteSubquery
from orchestrator.state import OrchestratorState
from tests.mocks.openai import (
    MockOpenAIClient,
    make_content_response,
    make_output_plan_response,
)

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

    assert set(schema["required"]) == {
        "resultEntities",
        "grainFields",
        "answerValues",
    }
    assert "uniqueItems" not in schema["properties"]["answerValues"]
    entity_schema = schema["properties"]["resultEntities"]["items"]
    assert "product" in entity_schema["properties"]["role"]["enum"]


def test_semantic_compiler_adds_display_identity_before_answer_values() -> None:
    plan = SemanticOutputPlan(
        result_entities=(
            PlannedResultEntity(
                role="product",
                representation="display",
                in_grain=True,
            ),
        ),
        grain_fields=(),
        answer_values=("listPrice", "standardCost"),
    )

    assert compile_semantic_output_plan(plan, _catalog(), tool="sql") == [
        "productId",
        "productName",
        "listPrice",
        "standardCost",
    ]


@pytest.mark.parametrize(
    ("tool", "role", "representation", "expected"),
    [
        ("sql", "supplier", "display", ["supplierId", "supplierName"]),
        ("graph", "component", "display", ["componentId", "componentName"]),
        ("sql", "workOrder", "display", ["workOrderId"]),
        ("graph", "supplier", "reference", ["supplierId"]),
    ],
)
def test_semantic_compiler_uses_role_projection_instead_of_id_suffix(
    tool: str,
    role: str,
    representation: Literal["display", "reference"],
    expected: list[str],
) -> None:
    plan = SemanticOutputPlan(
        result_entities=(
            PlannedResultEntity(
                role=role,
                representation=representation,
                in_grain=True,
            ),
        ),
        grain_fields=(),
        answer_values=(),
    )

    assert compile_semantic_output_plan(plan, _catalog(), tool=tool) == expected


def test_semantic_compiler_keeps_scalar_aggregate_without_entity_identity() -> None:
    plan = SemanticOutputPlan(
        result_entities=(),
        grain_fields=(),
        answer_values=("activeSupplierCount",),
    )

    assert compile_semantic_output_plan(plan, _catalog(), tool="sql") == [
        "activeSupplierCount"
    ]


def test_semantic_compiler_allows_empty_answer_for_structural_finalizer() -> None:
    plan = SemanticOutputPlan(
        result_entities=(),
        grain_fields=(),
        answer_values=(),
    )

    assert compile_semantic_output_plan(plan, _catalog(), tool="graph") == []


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
    client = MockOpenAIClient(make_output_plan_response(answer_values=selected))
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
        make_output_plan_response(
            result_entities=[
                {
                    "role": "product",
                    "representation": "display",
                    "inGrain": True,
                }
            ],
            answer_values=["color"],
        )
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
    assert result["subqueries"][0]["requiredOutputs"] == [
        "productId",
        "productName",
        "color",
    ]


async def test_downstream_non_identity_binding_is_added_to_producer_outputs() -> None:
    client = MockOpenAIClient(
        make_output_plan_response(
            result_entities=[
                {
                    "role": "component",
                    "representation": "display",
                    "inGrain": True,
                }
            ]
        ),
        make_output_plan_response(answer_values=["actualStock"]),
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
        "componentId",
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
            return make_output_plan_response(answer_values=[output])

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
        make_output_plan_response(answer_values=["productCount"]),
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


async def test_row_attributes_without_result_context_get_one_semantic_retry() -> None:
    client = MockOpenAIClient(
        make_output_plan_response(answer_values=["listPrice", "standardCost"]),
        make_output_plan_response(
            result_entities=[
                {
                    "role": "product",
                    "representation": "display",
                    "inGrain": True,
                }
            ],
            answer_values=["listPrice", "standardCost"],
        ),
    )
    subquery: RouteSubquery = {
        "id": "sql_product_cost",
        "tool": "sql",
        "question": "return the requested product attributes",
        "dependsOn": [],
        "joinKeys": [],
    }

    result = await make_plan_outputs_node(client, _catalog())(_state([subquery]))

    assert result["subqueries"][0]["requiredOutputs"] == [
        "productId",
        "productName",
        "listPrice",
        "standardCost",
    ]
    assert len(client.calls) == 2
    correction = client.calls[1]["messages"][-1]["content"]
    assert "failed structural validation" in correction
    assert "Gold" not in correction


async def test_display_entity_must_participate_in_row_attribute_grain() -> None:
    def product_plan(in_grain: bool) -> Any:
        return make_output_plan_response(
            result_entities=[
                {
                    "role": "product",
                    "representation": "display",
                    "inGrain": in_grain,
                }
            ],
            answer_values=["color"],
        )

    client = MockOpenAIClient(product_plan(False), product_plan(True))
    subquery: RouteSubquery = {
        "id": "sql_product_color",
        "tool": "sql",
        "question": "return the selected row attribute",
        "dependsOn": [],
        "joinKeys": [],
    }

    result = await make_plan_outputs_node(client, _catalog())(_state([subquery]))

    assert result["subqueries"][0]["requiredOutputs"] == [
        "productId",
        "productName",
        "color",
    ]
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
