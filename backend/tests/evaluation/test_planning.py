from typing import Any

import pytest

from orchestrator.planning import (
    BOM_SHORTAGE_GRAPH_OUTPUTS,
    BOM_SHORTAGE_SQL_OUTPUTS,
    parse_execution_plan,
    parse_route_draft,
    route_draft_json_schema,
    validate_result_transform,
    validate_route_subqueries,
    validate_subqueries,
)


def _step(
    subquery_id: str,
    *,
    tool: str = "graph",
    depends_on: list[str] | None = None,
    outputs: list[str] | None = None,
    join_keys: list[str] | None = None,
    bindings: dict[str, str] | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "id": subquery_id,
        "tool": tool,
        "question": "질문",
        "dependsOn": depends_on or [],
        "requiredOutputs": outputs or ["componentId"],
        "joinKeys": join_keys or [],
    }
    if bindings:
        result["inputBindings"] = bindings
    return result


@pytest.mark.parametrize(
    ("subqueries", "message"),
    [
        ([_step("same"), _step("same", tool="sql")], "중복"),
        ([_step("sql", depends_on=["missing"])], "존재하지 않는"),
        (
            [
                _step(
                    "a",
                    depends_on=["b"],
                    bindings={"ids": "b.componentId"},
                ),
                _step(
                    "b",
                    tool="sql",
                    depends_on=["a"],
                    bindings={"ids": "a.componentId"},
                ),
            ],
            "순환",
        ),
        ([_step("bad", join_keys=["missing"])], "requiredOutputs"),
        (
            [
                _step("graph"),
                _step(
                    "sql",
                    tool="sql",
                    depends_on=["graph"],
                    bindings={"ids": "other.componentId"},
                ),
            ],
            "dependsOn",
        ),
        (
            [
                _step("graph"),
                _step("sql", tool="sql", depends_on=["graph"]),
            ],
            "inputBindings",
        ),
    ],
)
def test_validate_subqueries_rejects_invalid_contracts(
    subqueries: list[dict], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        validate_subqueries(subqueries)


def test_parse_execution_plan_keeps_legacy_tool_plan_with_one_subquery() -> None:
    result = parse_execution_plan('["sql"]', "원래 질문")

    assert result["tool_plan"] == ["sql"]
    assert len(result["subqueries"]) == 1
    assert result["subqueries"][0]["question"] == "원래 질문"


def test_route_binding_name_must_match_schema_identity_alias() -> None:
    subqueries = [
        {
            "id": "graph_components",
            "tool": "graph",
            "question": "부품을 조회한다.",
            "dependsOn": [],
            "joinKeys": ["componentId"],
        },
        {
            "id": "sql_stock",
            "tool": "sql",
            "question": "재고를 조회한다.",
            "dependsOn": ["graph_components"],
            "joinKeys": ["componentId"],
            "inputBindings": {"productIds": "graph_components.componentId"},
        },
    ]

    with pytest.raises(ValueError, match="binding 이름과 source alias"):
        validate_route_subqueries(subqueries)


def test_parse_route_draft_merges_binding_alias_into_both_join_keys() -> None:
    content = """{
      "tool_plan": ["graph", "sql"],
      "subqueries": [
        {
          "id": "graph_supplier_impact",
          "tool": "graph",
          "question": "North Mill 공급 부품의 영향을 찾는다.",
          "dependsOn": [],
          "joinKeys": [],
          "inputBindings": {}
        },
        {
          "id": "sql_component_stock",
          "tool": "sql",
          "question": "앞 단계 부품의 재고를 찾는다.",
          "dependsOn": ["graph_supplier_impact"],
          "joinKeys": ["componentId"],
          "inputBindings": {
            "componentIds": "graph_supplier_impact.componentId"
          }
        }
      ],
      "resultTransform": null
    }"""

    result = parse_route_draft(content, "공급 중단 영향과 재고")

    assert result["subqueries"][0]["joinKeys"] == ["componentId"]
    assert result["subqueries"][1]["joinKeys"] == ["componentId"]


def test_route_schema_restricts_binding_value_to_matching_identity_alias() -> None:
    schema = route_draft_json_schema({"componentId", "productId"})
    variants = schema["properties"]["subqueries"]["items"]["properties"][
        "inputBindings"
    ]["anyOf"]
    component_variant = next(
        item for item in variants if "componentIds" in item["properties"]
    )

    assert component_variant["properties"]["componentIds"]["pattern"] == (
        r"^[^.\s]+\.componentId$"
    )


def test_parse_execution_plan_rejects_tool_order_before_its_dependency() -> None:
    content = """{
      "tool_plan": ["sql", "graph"],
      "subqueries": [
        {
          "id": "graph_impact",
          "tool": "graph",
          "question": "영향 부품을 찾는다.",
          "dependsOn": [],
          "requiredOutputs": ["componentId"],
          "joinKeys": ["componentId"]
        },
        {
          "id": "sql_stock",
          "tool": "sql",
          "question": "부품 재고를 찾는다.",
          "dependsOn": ["graph_impact"],
          "inputBindings": {"componentIds": "graph_impact.componentId"},
          "requiredOutputs": ["componentId"],
          "joinKeys": ["componentId"]
        }
      ]
    }"""

    with pytest.raises(ValueError, match="의존 실행 순서"):
        parse_execution_plan(content, "복합 질문")


def test_parse_execution_plan_rejects_more_than_one_subquery_for_same_tool() -> None:
    content = """{
      "tool_plan": ["sql"],
      "subqueries": [
        {
          "id": "sql_price",
          "tool": "sql",
          "question": "가격을 찾는다.",
          "dependsOn": [],
          "requiredOutputs": ["productId", "listPrice"],
          "joinKeys": []
        },
        {
          "id": "sql_stock",
          "tool": "sql",
          "question": "재고를 찾는다.",
          "dependsOn": [],
          "requiredOutputs": ["productId", "actualStock"],
          "joinKeys": []
        }
      ]
    }"""

    with pytest.raises(ValueError, match="도구 하나당 subquery"):
        parse_execution_plan(content, "가격과 재고")


def test_validate_subqueries_accepts_same_hybrid_join_keys_in_different_order() -> None:
    result = validate_subqueries(
        [
            _step(
                "graph",
                outputs=["componentId", "supplierId"],
                join_keys=["componentId", "supplierId"],
            ),
            _step(
                "sql",
                tool="sql",
                outputs=["supplierId", "componentId"],
                join_keys=["supplierId", "componentId"],
            ),
        ]
    )

    assert result[0]["joinKeys"] == ["componentId", "supplierId"]
    assert result[1]["joinKeys"] == ["supplierId", "componentId"]


@pytest.mark.parametrize(
    ("subqueries", "message"),
    [
        (
            [
                _step("graph", join_keys=["componentId"]),
                _step("sql", tool="sql"),
            ],
            "모두 지정하거나 모두 비워야",
        ),
        (
            [
                _step("graph", join_keys=["componentId"]),
                _step(
                    "sql",
                    tool="sql",
                    outputs=["supplierId"],
                    join_keys=["supplierId"],
                ),
            ],
            "구성이 일치",
        ),
        (
            [
                _step("graph"),
                _step(
                    "sql",
                    tool="sql",
                    depends_on=["graph"],
                    bindings={"ids": "graph.componentId"},
                ),
            ],
            "공통 joinKeys",
        ),
        (
            [
                _step("graph", join_keys=["componentId"]),
                _step(
                    "sql",
                    tool="sql",
                    depends_on=["graph"],
                    outputs=["componentId", "supplierId"],
                    join_keys=["supplierId"],
                    bindings={"ids": "graph.componentId"},
                ),
            ],
            "선행 단계에 없습니다",
        ),
    ],
)
def test_validate_subqueries_rejects_invalid_hybrid_join_contracts(
    subqueries: list[dict[str, Any]], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        validate_subqueries(subqueries)


def test_validate_subqueries_keeps_single_and_legacy_hybrid_compatible() -> None:
    single = validate_subqueries([_step("sql", tool="sql", join_keys=["componentId"])])
    legacy = parse_execution_plan('["sql", "graph"]', "독립 복합 질문")

    assert single[0]["joinKeys"] == ["componentId"]
    assert [item["joinKeys"] for item in legacy["subqueries"]] == [[], []]


def test_object_plan_requires_all_canonical_outputs_but_legacy_can_be_empty() -> None:
    content = """{
      "tool_plan": ["sql"],
      "subqueries": [{
        "id": "sql_stock",
        "tool": "sql",
        "question": "재고를 조회한다.",
        "dependsOn": [],
        "requiredOutputs": [],
        "joinKeys": [],
        "inputBindings": {}
      }]
    }"""

    with pytest.raises(ValueError, match="requiredOutputs는 비어 있을 수 없습니다"):
        parse_execution_plan(content, "재고")

    assert (
        parse_execution_plan('["sql"]', "재고")["subqueries"][0]["requiredOutputs"]
        == []
    )


def _shortage_steps() -> list[dict[str, Any]]:
    return [
        _step(
            "graph_bom",
            outputs=sorted(BOM_SHORTAGE_GRAPH_OUTPUTS),
            join_keys=["componentId"],
        ),
        _step(
            "sql_stock",
            tool="sql",
            depends_on=["graph_bom"],
            outputs=sorted(BOM_SHORTAGE_SQL_OUTPUTS),
            join_keys=["componentId"],
            bindings={"componentIds": "graph_bom.componentId"},
        ),
    ]


def test_bom_shortage_transform_requires_exact_allowlisted_plan() -> None:
    steps = validate_subqueries(_shortage_steps())

    assert validate_result_transform(
        {"type": "bom_shortage_v1", "productionQty": 10}, steps
    ) == {"type": "bom_shortage_v1", "productionQty": 10}


@pytest.mark.parametrize("production_qty", [True, 0, -1, float("inf"), float("nan")])
def test_bom_shortage_transform_rejects_invalid_production_quantity(
    production_qty: Any,
) -> None:
    steps = validate_subqueries(_shortage_steps())

    with pytest.raises(ValueError, match="유한한 양수"):
        validate_result_transform(
            {"type": "bom_shortage_v1", "productionQty": production_qty}, steps
        )


def test_bom_shortage_transform_rejects_wrong_binding_contract() -> None:
    steps = _shortage_steps()
    steps[1]["inputBindings"] = {"componentIds": "graph_bom.componentName"}
    validated = validate_subqueries(steps)

    with pytest.raises(ValueError, match="componentIds binding"):
        validate_result_transform(
            {"type": "bom_shortage_v1", "productionQty": 10}, validated
        )
