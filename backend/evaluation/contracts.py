"""생성된 라우팅·분할 계획과 evaluator manifest 계약 비교."""

import math
import re
from decimal import Decimal
from typing import Any

from evaluation.models import EvaluationCase, EvaluationContract
from orchestrator.bindings import collect_input_bindings as collect_input_bindings

_QUESTION_RELEVANCE_THRESHOLD = 0.02
_KOREAN_NUMBER_WORDS = {
    "1": ("하나", "한개", "한건", "한곳"),
    "2": ("둘", "두개", "두건", "두곳"),
    "3": ("셋", "세개", "세건", "세곳"),
    "4": ("넷", "네개", "네건", "네곳"),
    "5": ("다섯",),
    "6": ("여섯",),
    "7": ("일곱",),
    "8": ("여덟",),
    "9": ("아홉",),
    "10": ("열개", "열건", "열곳"),
}


def _question_matches(expected: str, actual: Any, source_question: str) -> bool:
    """Require a minimal deterministic lexical link to the expected intent."""
    if not isinstance(actual, str) or not actual.strip():
        return False

    def normalize(value: str) -> str:
        return re.sub(r"[^0-9A-Za-z가-힣]", "", value.casefold())

    expected_text = normalize(expected)
    actual_text = normalize(actual)
    if not expected_text or not actual_text:
        return False
    if expected_text == actual_text:
        return True

    actual_numbers = set(re.findall(r"\d+", actual_text))
    for number in set(re.findall(r"\d+", expected_text)):
        source_numbers = set(re.findall(r"\d+", normalize(source_question)))
        source_has_number = number in source_numbers or any(
            word in normalize(source_question)
            for word in _KOREAN_NUMBER_WORDS.get(number, ())
        )
        # Robustness/complexity wording may intentionally omit a canonical fixture
        # parameter (for example the default BOM depth). Do not require the router
        # to invent a constraint that was absent from the user's actual question.
        if not source_has_number:
            continue
        if number in actual_numbers:
            continue
        if not any(
            word in actual_text for word in _KOREAN_NUMBER_WORDS.get(number, ())
        ):
            return False

    def bigrams(value: str) -> set[str]:
        return {value[index : index + 2] for index in range(len(value) - 1)}

    expected_bigrams = bigrams(expected_text)
    actual_bigrams = bigrams(actual_text)
    union = expected_bigrams | actual_bigrams
    if not union:
        return False
    return (
        len(expected_bigrams & actual_bigrams) / len(union)
        >= _QUESTION_RELEVANCE_THRESHOLD
    )


def compare_execution_contract(
    contract: EvaluationContract,
    case: EvaluationCase,
    tool_plan: Any,
    subqueries: Any,
    result_transform: Any = None,
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
        actual_dependencies = actual.get("dependsOn")
        actual_join_keys = actual.get("joinKeys")

        def same_string_set(value: Any, expected_values: tuple[str, ...]) -> bool:
            return (
                isinstance(value, list)
                and all(isinstance(item, str) for item in value)
                and len(value) == len(set(value))
                and set(value) == set(expected_values)
            )

        structural_checks = {
            "tool": actual.get("tool") == expected.tool,
            "dependsOn": same_string_set(
                actual_dependencies,
                tuple(value for value in translated_dependencies if value),
            ),
            "joinKeys": (
                same_string_set(actual_join_keys, expected.join_keys)
                if contract.route == "HYBRID"
                else isinstance(actual_join_keys, list)
            ),
        }
        structural_checks["question"] = _question_matches(
            expected.question, actual.get("question"), case.question
        )
        step_checks.append(
            {
                "id": expected.id,
                "pass": all(structural_checks.values()),
                "checks": structural_checks,
            }
        )

    if (
        contract.final_result is not None
        and contract.final_result.transform is not None
    ):
        transform_pass = result_transform == {
            "type": contract.final_result.transform,
            "productionQty": case.parameters.get("productionQty"),
        }
    else:
        transform_pass = result_transform is None
    split_pass = (
        exact_steps and all(item["pass"] for item in step_checks) and transform_pass
    )
    return {
        "routingPass": routing_pass,
        "splitPass": split_pass,
        "exactSteps": exact_steps,
        "expectedIds": expected_ids,
        "actualIds": ids,
        "idMapping": mapping,
        "steps": step_checks,
        "transformPass": transform_pass,
    }


def compare_execution_plan_contract(
    contract: EvaluationContract,
    actual_subqueries: Any,
    id_mapping: dict[str, str],
) -> dict[str, Any]:
    """Compare output and binding contracts on the completed execution plan."""
    actual_items = (
        [item for item in actual_subqueries if isinstance(item, dict)]
        if isinstance(actual_subqueries, list)
        else []
    )
    actual_by_id = {item.get("id"): item for item in actual_items}
    exact_steps = len(actual_items) == len(contract.subqueries) and len(
        actual_by_id
    ) == len(contract.subqueries)
    steps: list[dict[str, Any]] = []

    for expected in contract.subqueries:
        actual_id = id_mapping.get(expected.id)
        actual = actual_by_id.get(actual_id)
        expected_outputs = set(expected.required_outputs)
        actual_outputs = (
            actual.get("requiredOutputs") if isinstance(actual, dict) else None
        )
        actual_output_set = (
            set(actual_outputs)
            if isinstance(actual_outputs, list)
            and all(isinstance(item, str) for item in actual_outputs)
            else set()
        )
        missing_outputs = sorted(expected_outputs - actual_output_set)
        extra_outputs = sorted(actual_output_set - expected_outputs)
        outputs_pass = (
            isinstance(actual_outputs, list)
            and all(isinstance(item, str) for item in actual_outputs)
            and len(actual_outputs) == len(actual_output_set)
            and not missing_outputs
        )
        outputs_exact_pass = outputs_pass and not extra_outputs

        expected_bindings = {
            key: f"{id_mapping.get(source.split('.', 1)[0])}.{source.split('.', 1)[1]}"
            for key, source in expected.input_bindings.items()
        }
        actual_bindings = (
            actual.get("inputBindings", {}) if isinstance(actual, dict) else None
        )
        missing_bindings = {
            key: value
            for key, value in expected_bindings.items()
            if not isinstance(actual_bindings, dict)
            or actual_bindings.get(key) != value
        }
        bindings_pass = actual_bindings == expected_bindings
        steps.append(
            {
                "id": expected.id,
                "actualId": actual_id,
                "requiredOutputs": {
                    "expected": sorted(expected_outputs),
                    "actual": actual_outputs,
                    "missing": missing_outputs,
                    "extra": extra_outputs,
                    "pass": outputs_pass,
                    "exactPass": outputs_exact_pass,
                },
                "inputBindings": {
                    "expected": expected_bindings,
                    "actual": actual_bindings,
                    "missing": missing_bindings,
                    "pass": bindings_pass,
                },
                "pass": outputs_pass and bindings_pass,
            }
        )

    required_outputs_pass = exact_steps and all(
        step["requiredOutputs"]["pass"] for step in steps
    )
    binding_pass = exact_steps and all(step["inputBindings"]["pass"] for step in steps)
    required_outputs_exact_pass = exact_steps and all(
        step["requiredOutputs"]["exactPass"] for step in steps
    )
    return {
        "pass": required_outputs_pass and binding_pass,
        "requiredOutputsPass": required_outputs_pass,
        "requiredOutputsExactPass": required_outputs_exact_pass,
        "bindingPass": binding_pass,
        "exactSteps": exact_steps,
        "steps": steps,
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
