from decimal import Decimal
from pathlib import Path

from evaluation.contracts import (
    compare_execution_contract,
    compare_execution_plan_contract,
    entity_matches,
)
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


def test_output_contract_does_not_change_route_split_score() -> None:
    manifest = load_manifest(PROJECT_ROOT / "queries" / "evaluation" / "manifest.json")
    contract = manifest.contracts["RQ01"]
    case = next(case for case in manifest.cases if case.case_id == "RQ01")
    actual = contract.subqueries[0].planning_shape()
    actual["requiredOutputs"] = []

    result = compare_execution_contract(contract, case, ["sql"], [actual])

    assert result["splitPass"] is True
    assert set(result["steps"][0]["checks"]) == {
        "tool",
        "dependsOn",
        "joinKeys",
        "question",
    }


def test_route_split_rejects_unrelated_nonempty_subquery_question() -> None:
    manifest = load_manifest(PROJECT_ROOT / "queries" / "evaluation" / "manifest.json")
    contract = manifest.contracts["RQ18"]
    case = next(case for case in manifest.cases if case.case_id == "RQ18")
    actual = [item.planning_shape() for item in contract.subqueries]
    for item in actual:
        item["question"] = "완전히 무관한 질문"

    result = compare_execution_contract(contract, case, ["graph", "sql"], actual)

    assert result["splitPass"] is False
    assert all(step["checks"]["question"] is False for step in result["steps"])


def test_route_question_does_not_invent_canonical_number_omitted_by_variant() -> None:
    manifest = load_manifest(PROJECT_ROOT / "queries" / "evaluation" / "manifest.json")
    contract = manifest.contracts["RQ17"]
    case = next(case for case in manifest.cases if case.case_id == "RB17-C")
    actual = [item.planning_shape() for item in contract.subqueries]
    actual[0][
        "question"
    ] = "Road-650 Black, 58과 Mountain-100 Black, 38의 공통 부품을 조회한다."

    result = compare_execution_contract(contract, case, ["graph"], actual)

    assert result["steps"][0]["checks"]["question"] is True
    assert result["splitPass"] is True


def test_route_question_accepts_entity_id_instead_of_number_bearing_name() -> None:
    manifest = load_manifest(PROJECT_ROOT / "queries" / "evaluation" / "manifest.json")
    contract = manifest.contracts["RQ19"]
    case = next(case for case in manifest.cases if case.case_id == "RB19-C")
    actual = [item.planning_shape() for item in contract.subqueries]
    actual[0][
        "question"
    ] = "제품 ID 680을 10개 생산할 때 필요한 BOM 경로와 공급업체를 조회한다."

    result = compare_execution_contract(
        contract,
        case,
        ["graph", "sql"],
        actual,
        {"type": "bom_shortage_v1", "productionQty": 10},
    )

    assert result["steps"][0]["checks"]["question"] is True
    assert result["splitPass"] is True


def test_route_question_still_requires_explicit_numeric_constraint() -> None:
    manifest = load_manifest(PROJECT_ROOT / "queries" / "evaluation" / "manifest.json")
    contract = manifest.contracts["RQ17"]
    case = next(case for case in manifest.cases if case.case_id == "RQ17")
    actual = [item.planning_shape() for item in contract.subqueries]
    actual[0][
        "question"
    ] = "Road-650 Black, 58과 Mountain-100 Black, 38의 공통 부품을 조회한다."

    result = compare_execution_contract(contract, case, ["graph"], actual)

    assert result["steps"][0]["checks"]["question"] is False
    assert result["splitPass"] is False


def test_hybrid_output_contract_is_reported_separately_from_split() -> None:
    manifest = load_manifest(PROJECT_ROOT / "queries" / "evaluation" / "manifest.json")
    contract = manifest.contracts["RQ18"]
    case = next(case for case in manifest.cases if case.case_id == "RQ18")
    actual = [item.planning_shape() for item in contract.subqueries]
    actual[0]["requiredOutputs"] = ["supplierId"]

    route_result = compare_execution_contract(contract, case, ["graph", "sql"], actual)
    plan_result = compare_execution_plan_contract(
        contract,
        actual,
        route_result["idMapping"],
    )

    assert route_result["splitPass"] is True
    assert plan_result["requiredOutputsPass"] is False
    assert plan_result["steps"][0]["requiredOutputs"]["actual"] == ["supplierId"]
    assert plan_result["steps"][0]["requiredOutputs"]["missing"] == [
        "componentId",
        "componentName",
        "depth",
        "finishedProductId",
        "finishedProductName",
        "pathProductIds",
    ]
    assert plan_result["steps"][0]["requiredOutputs"]["pass"] is False
    assert plan_result["steps"][0]["requiredOutputs"]["exactPass"] is False


def test_required_output_coverage_and_exactness_are_reported_separately() -> None:
    manifest = load_manifest(PROJECT_ROOT / "queries" / "evaluation" / "manifest.json")
    contract = manifest.contracts["RQ01"]
    case = next(case for case in manifest.cases if case.case_id == "RQ01")
    actual = [contract.subqueries[0].planning_shape()]
    actual[0]["requiredOutputs"].append("sellEndDate")
    route_result = compare_execution_contract(contract, case, ["sql"], actual)

    result = compare_execution_plan_contract(
        contract, actual, route_result["idMapping"]
    )

    details = result["steps"][0]["requiredOutputs"]
    assert details["pass"] is True
    assert details["exactPass"] is False
    assert details["extra"] == ["sellEndDate"]
    assert result["requiredOutputsPass"] is True
    assert result["requiredOutputsExactPass"] is False


def test_execution_plan_details_and_top_level_passes_cannot_disagree() -> None:
    manifest = load_manifest(PROJECT_ROOT / "queries" / "evaluation" / "manifest.json")
    contract = manifest.contracts["RQ18"]
    case = next(case for case in manifest.cases if case.case_id == "RQ18")
    actual = [item.planning_shape() for item in contract.subqueries]
    actual[1]["inputBindings"] = {"componentIds": "wrong.componentId"}
    route_result = compare_execution_contract(contract, case, ["graph", "sql"], actual)

    result = compare_execution_plan_contract(
        contract,
        actual,
        route_result["idMapping"],
    )

    assert result["requiredOutputsPass"] is all(
        step["requiredOutputs"]["pass"] for step in result["steps"]
    )
    assert result["requiredOutputsExactPass"] is all(
        step["requiredOutputs"]["exactPass"] for step in result["steps"]
    )
    assert result["bindingPass"] is all(
        step["inputBindings"]["pass"] for step in result["steps"]
    )
    assert result["pass"] is (result["requiredOutputsPass"] and result["bindingPass"])
    assert result["steps"][1]["inputBindings"] == {
        "expected": {"componentIds": "graph_impact.componentId"},
        "actual": {"componentIds": "wrong.componentId"},
        "missing": {"componentIds": "graph_impact.componentId"},
        "pass": False,
    }


def test_bom_shortage_contract_requires_the_case_production_quantity() -> None:
    manifest = load_manifest(PROJECT_ROOT / "queries" / "evaluation" / "manifest.json")
    contract = manifest.contracts["RQ19"]
    case = next(case for case in manifest.cases if case.case_id == "RQ19")
    actual = [item.planning_shape() for item in contract.subqueries]

    missing = compare_execution_contract(contract, case, ["graph", "sql"], actual)
    valid = compare_execution_contract(
        contract,
        case,
        ["graph", "sql"],
        actual,
        {"type": "bom_shortage_v1", "productionQty": 10},
    )

    assert missing["transformPass"] is False
    assert missing["splitPass"] is False
    assert valid["transformPass"] is True
    assert valid["splitPass"] is True
