from pathlib import Path
from types import MethodType
from typing import Any

from evaluation.models import load_manifest
from evaluation.normalization import normalize_rows, normalized_sha256
from evaluation.runner import EvaluationRunner

PROJECT_ROOT = Path(__file__).resolve().parents[3]


def _graph_row(component_id: int) -> dict[str, Any]:
    return {
        "supplierId": 1494,
        "supplierName": "Allenson Cycles",
        "componentId": component_id,
        "componentName": "Seat Post",
        "finishedProductId": 999,
        "finishedProductName": "Finished",
        "depth": 1,
        "pathProductIds": [component_id, 999],
    }


def _runner_with_stubs(
    generated_rows: dict[str, list[dict[str, Any]]],
    gold_rows: dict[str, list[dict[str, Any]]],
    calls: list[dict[str, Any]],
) -> EvaluationRunner:
    runner = object.__new__(EvaluationRunner)

    def generate(
        self: EvaluationRunner,
        expected: Any,
        actual: dict[str, Any],
        entity: Any,
        inputs: dict[str, list[Any]],
    ) -> str:
        calls.append({"kind": "generate", "id": expected.id, "inputs": inputs})
        return f"query:{expected.id}"

    def execute(
        self: EvaluationRunner,
        tool: str,
        query: str,
        parameters: dict[str, Any],
        max_rows: int,
    ) -> list[dict[str, Any]]:
        return generated_rows[query.removeprefix("query:")]

    def gold(
        self: EvaluationRunner,
        expected: Any,
        parameters: dict[str, Any],
    ) -> tuple[list[dict[str, Any]], str]:
        calls.append({"kind": "gold", "id": expected.id, "parameters": parameters})
        normalized = normalize_rows(
            gold_rows[expected.id],
            required_outputs=expected.required_outputs,
            aliases=expected.aliases,
            ordering=expected.ordering,
        )
        return normalized, normalized_sha256(normalized)

    runner._generate_query = MethodType(generate, runner)  # type: ignore[method-assign]
    runner._execute = MethodType(execute, runner)  # type: ignore[method-assign]
    runner._gold_result = MethodType(gold, runner)  # type: ignore[method-assign]
    return runner


def test_failed_upstream_result_blocks_downstream_without_duplicate_failure() -> None:
    manifest = load_manifest(PROJECT_ROOT / "queries" / "evaluation" / "manifest.json")
    contract = manifest.contracts["RQ18"]
    case = next(case for case in manifest.cases if case.case_id == "RQ18")
    actual = [item.planning_shape() for item in contract.subqueries]
    calls: list[dict[str, Any]] = []
    runner = _runner_with_stubs(
        {"graph_impact": [_graph_row(530)]},
        {"graph_impact": [_graph_row(531)]},
        calls,
    )

    records = runner._evaluate_subqueries(
        contract,
        case,
        actual,
        contract.expected_entities,
        {item.id: item.id for item in contract.subqueries},
    )

    assert records[0]["status"] == "FAIL"
    assert records[1]["status"] == "BLOCKED_BY_DEPENDENCY"
    assert [call["id"] for call in calls if call["kind"] == "generate"] == [
        "graph_impact"
    ]


def test_passed_upstream_values_are_forwarded_to_sql_generation_and_gold() -> None:
    manifest = load_manifest(PROJECT_ROOT / "queries" / "evaluation" / "manifest.json")
    contract = manifest.contracts["RQ18"]
    case = next(case for case in manifest.cases if case.case_id == "RQ18")
    actual = [item.planning_shape() for item in contract.subqueries]
    graph_rows = [_graph_row(530), _graph_row(530)]
    sql_rows = [{"componentId": 530, "actualStock": 780}]
    calls: list[dict[str, Any]] = []
    runner = _runner_with_stubs(
        {"graph_impact": graph_rows, "sql_stock": sql_rows},
        {"graph_impact": graph_rows, "sql_stock": sql_rows},
        calls,
    )

    records = runner._evaluate_subqueries(
        contract,
        case,
        actual,
        contract.expected_entities,
        {item.id: item.id for item in contract.subqueries},
    )

    assert [record["status"] for record in records] == ["PASS", "PASS"]
    assert records[1]["upstreamInputs"] == {"componentIds": [530, 530]}
    sql_generate = next(
        call
        for call in calls
        if call["kind"] == "generate" and call["id"] == "sql_stock"
    )
    assert sql_generate["inputs"] == {"componentIds": [530, 530]}
    sql_gold = next(
        call for call in calls if call["kind"] == "gold" and call["id"] == "sql_stock"
    )
    assert sql_gold["parameters"]["componentIds"] == [530, 530]
