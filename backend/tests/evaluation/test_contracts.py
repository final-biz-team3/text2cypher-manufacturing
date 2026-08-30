from decimal import Decimal
from pathlib import Path

from evaluation.contracts import compare_execution_contract, entity_matches
from evaluation.models import load_manifest

PROJECT_ROOT = Path(__file__).resolve().parents[3]


def test_entity_match_uses_required_identity_fields() -> None:
    expected = {"productId": 680, "productName": "HL Road Frame - Black, 58"}
    actual = [
        {
            "productId": 680,
            "productName": "  hl road frame - black, 58 ",
            "score": 1.0,
        }
    ]

    assert entity_matches(expected, actual) is True


def test_entity_match_accepts_equivalent_integral_id_number_types() -> None:
    assert entity_matches({"productId": 680}, {"productId": 680.0}) is True
    assert entity_matches({"productId": 680.0}, {"productId": 680}) is True
    assert entity_matches({"productId": Decimal("680")}, {"productId": 680}) is True


def test_entity_match_rejects_invalid_id_numeric_values() -> None:
    assert entity_matches({"productId": 680}, {"productId": True}) is False
    assert entity_matches({"productId": 680}, {"productId": "680"}) is False
    assert entity_matches({"productId": 680}, {"productId": 680.5}) is False
    assert entity_matches({"productId": 680}, {"productId": float("nan")}) is False
    assert entity_matches({"productId": 680}, {"productId": float("inf")}) is False


def test_entity_match_keeps_non_id_values_type_strict() -> None:
    assert entity_matches({"rank": 1}, {"rank": 1.0}) is False


def test_entity_match_normalizes_empty_values() -> None:
    assert entity_matches(None, []) is True
    assert entity_matches([], None) is True


def test_entity_match_requires_every_entity_in_question_order() -> None:
    first = {"productId": 765, "productName": "Road-650 Black, 58"}
    second = {"productId": 775, "productName": "Mountain-100 Black, 38"}

    assert entity_matches([first, second], [first]) is False
    assert entity_matches([first, second], [second, first]) is False


def test_hybrid_plan_accepts_semantic_ids_and_valid_dependency_mapping() -> None:
    manifest = load_manifest(PROJECT_ROOT / "queries" / "evaluation" / "manifest.json")
    contract = manifest.contracts["RQ18"]
    case = next(case for case in manifest.cases if case.case_id == "RQ18")
    graph, sql = contract.subqueries
    actual = [
        {
            **graph.planning_shape(),
            "id": "supplier_paths",
        },
        {
            **sql.planning_shape(),
            "id": "component_stock",
            "dependsOn": ["supplier_paths"],
            "inputBindings": {"componentIds": "supplier_paths.componentId"},
        },
    ]

    result = compare_execution_contract(contract, case, ["graph", "sql"], actual)

    assert result["routingPass"] is True
    assert result["splitPass"] is True
    assert result["idMapping"] == {
        "graph_impact": "supplier_paths",
        "sql_stock": "component_stock",
    }


def test_hybrid_plan_detects_wrong_database_assignment() -> None:
    manifest = load_manifest(PROJECT_ROOT / "queries" / "evaluation" / "manifest.json")
    contract = manifest.contracts["RQ20"]
    case = next(case for case in manifest.cases if case.case_id == "RQ20")
    actual = [item.planning_shape() for item in contract.subqueries]
    actual[0]["tool"] = "graph"

    result = compare_execution_contract(contract, case, ["sql", "graph"], actual)

    assert result["splitPass"] is False
    assert result["steps"][0]["checks"]["tool"] is False


def test_parallel_hybrid_plan_accepts_equivalent_step_order() -> None:
    manifest = load_manifest(PROJECT_ROOT / "queries" / "evaluation" / "manifest.json")
    contract = manifest.contracts["RQ20"]
    case = next(case for case in manifest.cases if case.case_id == "RQ20")
    actual = [item.planning_shape() for item in reversed(contract.subqueries)]

    result = compare_execution_contract(contract, case, ["graph", "sql"], actual)

    assert result["routingPass"] is True
    assert result["splitPass"] is True


def test_hybrid_planning_fields_are_compared_as_sets_not_display_order() -> None:
    manifest = load_manifest(PROJECT_ROOT / "queries" / "evaluation" / "manifest.json")
    contract = manifest.contracts["RQ18"]
    case = next(case for case in manifest.cases if case.case_id == "RQ18")
    actual = [item.planning_shape() for item in contract.subqueries]
    actual[0]["requiredOutputs"] = list(reversed(actual[0]["requiredOutputs"]))
    actual[0]["joinKeys"] = list(reversed(actual[0]["joinKeys"]))

    result = compare_execution_contract(contract, case, ["graph", "sql"], actual)

    assert result["splitPass"] is True


def test_single_query_requires_router_to_return_complete_result_schema() -> None:
    manifest = load_manifest(PROJECT_ROOT / "queries" / "evaluation" / "manifest.json")
    contract = manifest.contracts["RQ01"]
    case = next(case for case in manifest.cases if case.case_id == "RQ01")
    actual = contract.subqueries[0].planning_shape()
    actual["requiredOutputs"] = []

    result = compare_execution_contract(contract, case, ["sql"], [actual])

    assert result["splitPass"] is False
    assert result["steps"][0]["checks"]["requiredOutputs"] is False


def test_hybrid_plan_must_keep_handoff_and_join_output() -> None:
    manifest = load_manifest(PROJECT_ROOT / "queries" / "evaluation" / "manifest.json")
    contract = manifest.contracts["RQ18"]
    case = next(case for case in manifest.cases if case.case_id == "RQ18")
    actual = [item.planning_shape() for item in contract.subqueries]
    actual[0]["requiredOutputs"] = ["supplierId"]

    result = compare_execution_contract(contract, case, ["graph", "sql"], actual)

    assert result["splitPass"] is False
    assert result["steps"][0]["missingPlanningOutputs"] == [
        "componentId",
        "componentName",
        "depth",
        "finishedProductId",
        "finishedProductName",
        "pathProductIds",
    ]
