from typing import Any

import pytest

from orchestrator.planning import parse_execution_plan, validate_subqueries


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
                _step("a", depends_on=["b"]),
                _step("b", tool="sql", depends_on=["a"]),
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
