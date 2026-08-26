import json
import re
from pathlib import Path

from evaluation.models import load_manifest

PROJECT_ROOT = Path(__file__).resolve().parents[3]


def test_manifest_covers_all_rq_contracts_and_two_suites() -> None:
    manifest = load_manifest(PROJECT_ROOT / "queries" / "evaluation" / "manifest.json")

    assert set(manifest.contracts) == {f"RQ{number:02d}" for number in range(1, 21)}
    assert sum(case.suite == "canonical" for case in manifest.cases) == 20
    assert sum(case.suite == "robustness" for case in manifest.cases) == 20
    assert all(contract.subqueries for contract in manifest.contracts.values())
    assert (
        "active_vendor_count"
        in manifest.contracts["RQ03"].subqueries[0].aliases["activeSupplierCount"]
    )


def test_hybrid_contracts_are_partial_query_evaluated() -> None:
    manifest = load_manifest(PROJECT_ROOT / "queries" / "evaluation" / "manifest.json")

    for contract_id in ("RQ18", "RQ19", "RQ20"):
        contract = manifest.contracts[contract_id]
        assert contract.support_status == "QUERY_EVALUATED_FINAL_JOIN_PENDING"
        assert {subquery.tool for subquery in contract.subqueries} == {"sql", "graph"}

    assert manifest.contracts["RQ18"].subqueries[1].input_bindings == {
        "componentIds": "graph_impact.componentId"
    }
    assert manifest.contracts["RQ20"].subqueries[0].depends_on == ()
    assert manifest.contracts["RQ20"].subqueries[1].depends_on == ()


def test_manifest_routes_support_and_step_counts_match_rq_contract() -> None:
    manifest = load_manifest(PROJECT_ROOT / "queries" / "evaluation" / "manifest.json")

    for number in range(1, 21):
        contract = manifest.contracts[f"RQ{number:02d}"]
        if number <= 11:
            assert contract.route == "SQL"
        elif number <= 17:
            assert contract.route == "GRAPH"
        else:
            assert contract.route == "HYBRID"
        assert contract.support_status == (
            "FULLY_EVALUATED" if number <= 17 else "QUERY_EVALUATED_FINAL_JOIN_PENDING"
        )
        assert len(contract.subqueries) == (1 if number <= 17 else 2)


def test_gold_parameters_are_available_from_case_or_upstream_binding() -> None:
    manifest = load_manifest(PROJECT_ROOT / "queries" / "evaluation" / "manifest.json")
    canonical = {
        case.contract_id: case for case in manifest.cases if case.suite == "canonical"
    }

    for contract_id, contract in manifest.contracts.items():
        case = canonical[contract_id]
        for subquery in contract.subqueries:
            gold = subquery.gold_file.read_text(encoding="utf-8")
            referenced = set(re.findall(r"%\((\w+)\)s", gold))
            referenced.update(re.findall(r"\$(\w+)", gold))
            available = set(case.parameters) | set(subquery.input_bindings)
            assert referenced <= available


def test_gold_header_explains_the_contract_and_stays_in_sync() -> None:
    manifest = load_manifest(PROJECT_ROOT / "queries" / "evaluation" / "manifest.json")

    for contract_id, contract in manifest.contracts.items():
        for subquery in contract.subqueries:
            comment = "--" if subquery.gold_file.suffix == ".sql" else "//"
            expected = f"{comment} {contract_id} {subquery.id}: {subquery.question}\n"
            assert subquery.gold_file.read_text(encoding="utf-8").startswith(expected)


def test_result_aliases_are_unambiguous_within_each_subquery() -> None:
    manifest = load_manifest(PROJECT_ROOT / "queries" / "evaluation" / "manifest.json")

    for contract in manifest.contracts.values():
        for subquery in contract.subqueries:
            owners: dict[str, str] = {}
            for output in subquery.required_outputs:
                for alias in (output, *subquery.aliases[output]):
                    normalized = re.sub(r"[^\w]", "", alias).replace("_", "").casefold()
                    assert owners.setdefault(normalized, output) == output


def test_hybrid_responsibilities_dependencies_and_join_keys_are_locked() -> None:
    manifest = load_manifest(PROJECT_ROOT / "queries" / "evaluation" / "manifest.json")
    expected = {
        "RQ18": [
            ("graph", (), ("componentId",), {}),
            (
                "sql",
                ("graph_impact",),
                ("componentId",),
                {"componentIds": "graph_impact.componentId"},
            ),
        ],
        "RQ19": [
            ("graph", (), ("componentId",), {}),
            (
                "sql",
                ("graph_bom_supply",),
                ("componentId",),
                {"componentIds": "graph_bom_supply.componentId"},
            ),
        ],
        "RQ20": [
            ("sql", (), ("workOrderId",), {}),
            ("graph", (), ("workOrderId",), {}),
        ],
    }

    for contract_id, expected_steps in expected.items():
        actual_steps = [
            (
                subquery.tool,
                subquery.depends_on,
                subquery.join_keys,
                subquery.input_bindings,
            )
            for subquery in manifest.contracts[contract_id].subqueries
        ]
        assert actual_steps == expected_steps


def test_evaluation_manifest_stays_aligned_with_query_contracts() -> None:
    manifest = load_manifest(PROJECT_ROOT / "queries" / "evaluation" / "manifest.json")
    source = json.loads(
        (PROJECT_ROOT / "queries" / "query_contracts.json").read_text(encoding="utf-8")
    )
    source_by_id = {item["id"]: item for item in source["questions"]}
    canonical = {
        case.contract_id: case for case in manifest.cases if case.suite == "canonical"
    }

    for contract_id, contract in manifest.contracts.items():
        source_contract = source_by_id[contract_id]
        assert source_contract["route"] == contract.route
        assert source_contract["sampleQuestion"] == canonical[contract_id].question
        if contract.route != "HYBRID":
            assert set(source_contract["requiredAnswerFields"]) == set(
                contract.subqueries[0].required_outputs
            )
