"""생성된 라우팅·분할 계획과 evaluator manifest 계약 비교."""

import math
from decimal import Decimal
from typing import Any

from evaluation.models import EvaluationCase, EvaluationContract


def compare_execution_contract(
    contract: EvaluationContract,
    _case: EvaluationCase,
    tool_plan: Any,
    subqueries: Any,
) -> dict[str, Any]:
    """route와 HYBRID 전달·결합에 필요한 최소 계약만 판정한다."""
    routing_pass = (
        isinstance(tool_plan, list)
        and len(tool_plan) == len(set(tool_plan))
        and set(contract.tool_plan) == set(tool_plan)
    )
    actual_items = (
        [item for item in subqueries if isinstance(item, dict)]
        if isinstance(subqueries, list)
        else []
    )
    actual_by_id = {item.get("id"): item for item in actual_items}
    ids = (
        [item.get("id") for item in subqueries] if isinstance(subqueries, list) else []
    )
    unique_ids = len(ids) == len(set(ids))
    expected_ids = [item.id for item in contract.subqueries]
    exact_steps = unique_ids and len(actual_items) == len(contract.subqueries)
    mapping: dict[str, str] = {}
    mapped_actual_ids: set[str] = set()
    for expected in contract.subqueries:
        same_id = actual_by_id.get(expected.id)
        if isinstance(same_id, dict):
            mapping[expected.id] = expected.id
            mapped_actual_ids.add(expected.id)
            continue
        candidates = [
            item
            for item in actual_items
            if item.get("tool") == expected.tool
            and isinstance(item.get("id"), str)
            and item["id"] not in mapped_actual_ids
        ]
        if len(candidates) == 1:
            actual_id = candidates[0]["id"]
            mapping[expected.id] = actual_id
            mapped_actual_ids.add(actual_id)

    step_checks: list[dict[str, Any]] = []
    for expected in contract.subqueries:
        actual_id = mapping.get(expected.id)
        actual = actual_by_id.get(actual_id)
        if not isinstance(actual, dict):
            step_checks.append(
                {"id": expected.id, "pass": False, "error": "MISSING_SUBQUERY"}
            )
            continue
        translated_dependencies = [mapping.get(value) for value in expected.depends_on]
        translated_bindings = {
            key: f"{mapping.get(source.split('.', 1)[0])}.{source.split('.', 1)[1]}"
            for key, source in expected.input_bindings.items()
        }
        actual_dependencies = actual.get("dependsOn")
        actual_outputs = actual.get("requiredOutputs")
        actual_join_keys = actual.get("joinKeys")

        planning_outputs = set(expected.required_outputs)

        def same_string_set(value: Any, expected_values: tuple[str, ...]) -> bool:
            return (
                isinstance(value, list)
                and all(isinstance(item, str) for item in value)
                and len(value) == len(set(value))
                and set(value) == set(expected_values)
            )

        actual_output_set = (
            set(actual_outputs) if isinstance(actual_outputs, list) else set()
        )
        essential_checks = {
            "tool": actual.get("tool") == expected.tool,
            "dependsOn": same_string_set(
                actual_dependencies,
                tuple(value for value in translated_dependencies if value),
            ),
            "requiredOutputs": (
                isinstance(actual_outputs, list)
                and len(actual_outputs) == len(actual_output_set)
                and planning_outputs.issubset(actual_output_set)
            ),
            "joinKeys": (
                same_string_set(actual_join_keys, expected.join_keys)
                if contract.route == "HYBRID"
                else isinstance(actual_join_keys, list)
            ),
            "inputBindings": (
                actual.get("inputBindings", {}) == translated_bindings
                if contract.route == "HYBRID"
                else True
            ),
        }
        essential_checks["question"] = isinstance(actual.get("question"), str) and bool(
            actual["question"].strip()
        )
        step_checks.append(
            {
                "id": expected.id,
                "pass": all(essential_checks.values()),
                "checks": essential_checks,
                "planningRequiredOutputs": sorted(planning_outputs),
                "missingPlanningOutputs": sorted(planning_outputs - actual_output_set),
            }
        )

    split_pass = exact_steps and all(item["pass"] for item in step_checks)
    return {
        "routingPass": routing_pass,
        "splitPass": split_pass,
        "exactSteps": exact_steps,
        "expectedIds": expected_ids,
        "actualIds": ids,
        "idMapping": mapping,
        "steps": step_checks,
    }


def _entity_items(value: Any) -> list[dict[str, Any]] | None:
    if value is None or value == []:
        return []
    if isinstance(value, dict):
        return [value]
    if isinstance(value, list) and all(isinstance(item, dict) for item in value):
        return value
    return None


def _normalized_integral_id(value: Any) -> Decimal | None:
    if isinstance(value, bool) or not isinstance(value, int | float | Decimal):
        return None
    if isinstance(value, float):
        if not math.isfinite(value):
            return None
        normalized = Decimal(str(value))
    else:
        normalized = Decimal(value)
    if not normalized.is_finite() or normalized != normalized.to_integral_value():
        return None
    return normalized


def _is_entity_id_field(field: str) -> bool:
    return field == "id" or field.endswith(("Id", "ID", "_id"))


def _entity_value_matches(field: str, expected: Any, actual: Any) -> bool:
    if field.casefold().endswith("name"):
        return (
            isinstance(expected, str)
            and isinstance(actual, str)
            and " ".join(expected.casefold().split())
            == " ".join(actual.casefold().split())
        )
    if _is_entity_id_field(field) and (
        isinstance(expected, int | float | Decimal)
        or isinstance(actual, int | float | Decimal)
    ):
        normalized_expected = _normalized_integral_id(expected)
        normalized_actual = _normalized_integral_id(actual)
        return (
            normalized_expected is not None
            and normalized_actual is not None
            and normalized_expected == normalized_actual
        )
    return type(expected) is type(actual) and expected == actual


def entity_matches(expected: Any, actual: Any) -> bool:
    """필수 identity와 복수 엔티티의 질문 등장 순서를 비교한다."""
    expected_items = _entity_items(expected)
    actual_items = _entity_items(actual)
    if expected_items is None or actual_items is None:
        return False
    if len(expected_items) != len(actual_items):
        return False
    return all(
        all(
            field in actual_item
            and _entity_value_matches(field, expected_value, actual_item[field])
            for field, expected_value in expected_item.items()
        )
        for expected_item, actual_item in zip(expected_items, actual_items, strict=True)
    )


def collect_input_bindings(
    bindings: dict[str, str],
    upstream_rows: dict[str, list[dict[str, Any]]],
) -> dict[str, list[Any]]:
    """선행 결과의 계약 필드 값을 중복 제거해 후속 입력으로 전달한다."""
    result: dict[str, list[Any]] = {}
    for input_name, source in bindings.items():
        dependency_id, field = source.split(".", 1)
        values: list[Any] = []
        for row in upstream_rows[dependency_id]:
            value = row[field]
            if value not in values:
                values.append(value)
        result[input_name] = values
    return result
