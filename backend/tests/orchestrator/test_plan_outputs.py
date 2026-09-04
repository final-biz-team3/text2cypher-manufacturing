"""손실 없는 output 계획과 구조적 마무리 계약을 테스트한다."""

import asyncio
from functools import lru_cache
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from agents.cypher.schema.loader import load_graph_schema
from agents.sql.schema.loader import load_sql_schema
from orchestrator.nodes.plan_outputs import (
    SemanticOutputPlan,
    compile_graph_generator_rules,
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
    graph_schema = output_plan_json_schema(_catalog(), "graph")

    assert "uniqueItems" not in schema["properties"]["requiredOutputs"]
    assert schema["properties"]["requiredOutputs"]["minItems"] == 1
    assert "minItems" not in graph_schema["properties"]["requiredOutputs"]
    assert schema["required"] == ["requiredOutputs", "displayEntities"]
    assert graph_schema["required"] == ["requiredOutputs", "displayEntities"]
    assert "graphRelations" not in graph_schema["properties"]


async def test_prompt_uses_composable_roles_without_query_family_recipes() -> None:
    client = MockOpenAIClient(
        make_output_plan_response(
            required_outputs=[],
            display_entities=["component", "finishedProduct"],
        )
    )
    question = "부품을 사용하는 완제품을 최대 4단계까지 알려줘"
    subquery: RouteSubquery = {
        "id": "graph_component_usage",
        "tool": "graph",
        "question": "ROUTE_SCOPE_SENTINEL 경로 정보를 포함한다",
        "dependsOn": [],
        "joinKeys": [],
    }

    await make_plan_outputs_node(client, _catalog())(_state([subquery], query=question))

    system_prompt = client.calls[0]["messages"][0]["content"]
    user_prompt = client.calls[0]["messages"][1]["content"]
    assert "anchor에서" in system_prompt
    assert "ordered path\n  projection의 방향 계약이 아닙니다" in system_prompt
    assert "고정 출력 묶음" in system_prompt
    assert "공급업체의 BOM 영향" not in system_prompt
    assert "작업지시의 공정과 작업장" not in system_prompt
    assert "resultGrain" not in system_prompt
    assert "provenance" in system_prompt
    assert "cardinality" in system_prompt
    assert "고정된 anchor라도 결과 문맥과 grain" in system_prompt
    assert "graphRelations" not in system_prompt
    assert "graphQualifiers" not in system_prompt
    assert "Cypher 생성 단계에서 결정합니다" in system_prompt
    assert "물리 관계와 방향은 Cypher 생성 단계" in system_prompt
    assert "path-preserving traversal" in system_prompt
    assert "서로 다른 전체 경로가 결과 행을" in system_prompt
    assert "짧은 endpoint 목록 표현이어도" in system_prompt
    assert "depth와 ordered ID path" in system_prompt
    assert "교집합·비교 결과" in system_prompt
    assert "minimum path\n  length" in system_prompt
    assert "계층·트리의\n  조상-자손 구조" in system_prompt
    assert "사람이 읽는 결과 표현의 일부이면 ordered name path" in system_prompt
    assert "endpoint 목록만 요구하면 name path는 추가하지 않습니다" in system_prompt
    assert "순서값 자체를 결과로 요구" in system_prompt
    assert question in user_prompt
    assert "ROUTE_SCOPE_SENTINEL" not in user_prompt


async def test_single_source_raw_route_is_non_authoritative_clarification() -> None:
    client = MockOpenAIClient(
        make_output_plan_response(
            required_outputs=[
                "productId",
                "productName",
                "locationId",
                "locationName",
                "shelf",
                "bin",
                "quantity",
            ]
        )
    )
    original = "제품 재고 어디에 몇 개 있어?"
    clarified = "제품의 위치 ID·이름, 선반, 보관함, 수량을 조회한다."
    subquery: RouteSubquery = {
        "id": "sql_inventory",
        "tool": "sql",
        "question": original,
        "dependsOn": [],
        "joinKeys": [],
    }
    state = _state([subquery], query=original)
    state["rawRouteDraft"] = {
        "subqueries": [
            {
                "id": "sql_inventory",
                "tool": "sql",
                "question": clarified,
                "dependsOn": [],
                "joinKeys": [],
                "inputBindings": [],
            }
        ],
        "resultTransform": None,
    }

    result = await make_plan_outputs_node(client, _catalog())(state)

    prompt = client.calls[0]["messages"][1]["content"]
    assert f"original question: {original}" in prompt
    assert f"clarified interpretation: {clarified}" in prompt
    assert result["subqueries"][0]["question"] == original
    assert result["subqueries"][0]["requiredOutputs"] == [
        "productId",
        "productName",
        "locationId",
        "locationName",
        "shelf",
        "bin",
        "quantity",
    ]


async def test_sql_prompt_preserves_complete_direct_output_selection() -> None:
    client = MockOpenAIClient(
        make_output_plan_response(
            required_outputs=[
                "productId",
                "productName",
                "actualStock",
                "safetyStockLevel",
                "shortageQty",
            ],
            display_entities=["product"],
        )
    )
    question = "제품의 실제 재고가 안전재고보다 얼마나 부족한지 보여줘"
    subquery: RouteSubquery = {
        "id": "sql_shortage",
        "tool": "sql",
        "question": question,
        "dependsOn": [],
        "joinKeys": [],
    }

    result = await make_plan_outputs_node(client, _catalog())(
        _state([subquery], query=question)
    )

    system_prompt = client.calls[0]["messages"][0]["content"]
    assert "완전하게 직접 선택" in system_prompt
    assert "requiredOutputs를 대체하지 않습니다" in system_prompt
    assert "정렬이나 top-N만으로" in system_prompt
    assert "graphResultMode" not in system_prompt
    assert result["subqueries"][0]["requiredOutputs"] == [
        "productId",
        "productName",
        "actualStock",
        "safetyStockLevel",
        "shortageQty",
    ]


async def test_graph_display_only_plan_compiles_entity_identity() -> None:
    client = MockOpenAIClient(
        make_output_plan_response(
            required_outputs=[],
            display_entities=["product"],
        )
    )
    subquery: RouteSubquery = {
        "id": "graph_products",
        "tool": "graph",
        "question": "제품을 알려줘",
        "dependsOn": [],
        "joinKeys": [],
    }

    result = await make_plan_outputs_node(client, _catalog())(_state([subquery]))

    assert result["subqueries"][0]["requiredOutputs"] == [
        "productId",
        "productName",
    ]


async def test_sql_display_only_plan_is_retried_as_incomplete() -> None:
    client = MockOpenAIClient(
        make_output_plan_response(
            required_outputs=[],
            display_entities=["product"],
        ),
        make_output_plan_response(
            required_outputs=["productId", "productName"],
            display_entities=["product"],
        ),
    )
    subquery: RouteSubquery = {
        "id": "sql_products",
        "tool": "sql",
        "question": "제품을 알려줘",
        "dependsOn": [],
        "joinKeys": [],
    }

    result = await make_plan_outputs_node(client, _catalog())(_state([subquery]))

    assert len(client.calls) == 2
    assert result["subqueries"][0]["requiredOutputs"] == [
        "productId",
        "productName",
    ]


def test_graph_role_without_path_adds_no_generator_rule() -> None:
    plan = SemanticOutputPlan(
        required_outputs=(),
        display_entities=("finishedProduct",),
    )
    selected = compile_semantic_output_plan(plan, _catalog(), tool="graph")

    assert compile_graph_generator_rules(plan, _catalog(), selected) == []


def test_non_path_graph_plan_adds_no_generator_rule() -> None:
    plan = SemanticOutputPlan(
        required_outputs=("sequence",),
        display_entities=("workOrder", "routingOperation", "location"),
    )
    selected = compile_semantic_output_plan(plan, _catalog(), tool="graph")

    assert compile_graph_generator_rules(plan, _catalog(), selected) == []


def test_graph_path_generator_rule_does_not_treat_display_order_as_direction() -> None:
    catalog = _catalog()
    component_first = SemanticOutputPlan(
        required_outputs=("depth", "pathProductIds"),
        display_entities=("component", "finishedProduct"),
    )

    rules = compile_graph_generator_rules(
        component_first,
        catalog,
        compile_semantic_output_plan(component_first, catalog, tool="graph"),
    )

    assert len(rules) == 1
    assert "component" not in rules[0]
    assert "finishedProduct" not in rules[0]
    assert "displayEntities 순서" in rules[0]
    assert "required output 순서" in rules[0]
    assert "물리 MATCH 방향" in rules[0]


def test_graph_path_evidence_remains_a_direct_required_output() -> None:
    plan = SemanticOutputPlan(
        required_outputs=("depth", "pathProductIds"),
        display_entities=("component", "finishedProduct"),
    )

    assert compile_semantic_output_plan(plan, _catalog(), tool="graph") == [
        "depth",
        "pathProductIds",
        "componentId",
        "componentName",
        "finishedProductId",
        "finishedProductName",
    ]


def test_physical_scalar_does_not_add_an_implicit_owner_role() -> None:
    plan = SemanticOutputPlan(
        required_outputs=("sequence",),
        display_entities=("workOrder", "location"),
    )

    assert compile_semantic_output_plan(plan, _catalog(), tool="graph") == [
        "sequence",
        "workOrderId",
        "locationId",
        "locationName",
    ]


def test_semantic_compiler_adds_identity_without_replacing_direct_selection() -> None:
    plan = SemanticOutputPlan(
        required_outputs=("listPrice", "standardCost"),
        display_entities=("product",),
    )

    assert compile_semantic_output_plan(plan, _catalog(), tool="sql") == [
        "listPrice",
        "standardCost",
        "productId",
        "productName",
    ]


def test_sql_compiler_does_not_infer_identity_from_a_physical_scalar() -> None:
    plan = SemanticOutputPlan(
        required_outputs=("listPrice",),
        display_entities=(),
    )

    assert compile_semantic_output_plan(plan, _catalog(), tool="sql") == ["listPrice"]


def test_semantic_compiler_does_not_expand_derived_inputs_or_path_bundles() -> None:
    shortage_plan = SemanticOutputPlan(
        required_outputs=("shortageQty",),
        display_entities=("product",),
    )
    path_plan = SemanticOutputPlan(
        required_outputs=("minDepth",),
        display_entities=("component",),
    )

    assert compile_semantic_output_plan(shortage_plan, _catalog(), tool="sql") == [
        "shortageQty",
        "productId",
        "productName",
    ]
    assert compile_semantic_output_plan(path_plan, _catalog(), tool="graph") == [
        "minDepth",
        "componentId",
        "componentName",
    ]


def test_scalar_output_without_display_entity_is_unchanged() -> None:
    plan = SemanticOutputPlan(
        required_outputs=("productCount",),
        display_entities=(),
    )

    assert compile_semantic_output_plan(plan, _catalog(), tool="sql") == [
        "productCount"
    ]


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
        make_output_plan_response(
            required_outputs=selected,
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
        make_output_plan_response(required_outputs=["productId", "color"])
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
        make_output_plan_response(
            required_outputs=["componentName"],
        ),
        make_output_plan_response(required_outputs=["actualStock"]),
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


async def test_hybrid_output_planning_uses_scope_only_as_source_responsibility() -> (
    None
):
    client = MockOpenAIClient(
        make_output_plan_response(
            required_outputs=["depth", "pathProductIds"],
            display_entities=["component", "finishedProduct"],
        ),
        make_output_plan_response(
            required_outputs=["componentId", "actualStock"],
        ),
    )
    original = (
        "공급업체의 공급 중단 시 영향을 받는 부품과 완제품, 각 부품의 재고를 알려줘."
    )
    graph_scope = "공급 부품과 영향을 받는 완제품 관계를 조회한다."
    sql_scope = "영향 부품별 현재 재고를 조회한다."
    subqueries: list[RouteSubquery] = [
        {
            "id": "graph_impact",
            "tool": "graph",
            "question": graph_scope,
            "dependsOn": [],
            "joinKeys": ["componentId"],
        },
        {
            "id": "sql_stock",
            "tool": "sql",
            "question": sql_scope,
            "dependsOn": ["graph_impact"],
            "joinKeys": ["componentId"],
            "inputBindings": {"componentIds": "graph_impact.componentId"},
        },
    ]

    result = await make_plan_outputs_node(client, _catalog())(
        _state(subqueries, query=original)
    )

    graph_prompt = client.calls[0]["messages"][1]["content"]
    sql_prompt = client.calls[1]["messages"][1]["content"]
    assert f"original question: {original}" in graph_prompt
    assert f"source responsibility: {graph_scope}" in graph_prompt
    assert f"original question: {original}" in sql_prompt
    assert f"source responsibility: {sql_scope}" in sql_prompt
    assert result["subqueries"][0]["requiredOutputs"] == [
        "depth",
        "pathProductIds",
        "componentId",
        "componentName",
        "finishedProductId",
        "finishedProductName",
    ]
    assert result["subqueries"][1]["requiredOutputs"] == [
        "componentId",
        "actualStock",
    ]


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
            output = "productCount" if tool == "sql" else "sequence"
            return make_output_plan_response(
                required_outputs=[output],
            )

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
        ["sequence"],
    ]


async def test_structural_retry_does_not_add_an_extra_planning_stage() -> None:
    client = MockOpenAIClient(
        make_output_plan_response(required_outputs=["pathProductIds"]),
        make_output_plan_response(required_outputs=["productCount"]),
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
    graph_rules = " ".join(result["subqueries"][0]["generatorRules"])
    assert "parent assembly별 relationship 원본 값" in graph_rules
    assert "composer가 productionQty를 정확히 한 번 적용" in graph_rules
