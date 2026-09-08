import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

from evaluation.models import load_manifest

PROJECT_ROOT = Path(__file__).resolve().parents[3]


def _digest(value: object) -> str:
    serialized = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(serialized.encode()).hexdigest()


def _effective_aliases(prefix: str) -> dict[str, object]:
    manifest = load_manifest(PROJECT_ROOT / "queries" / "evaluation" / "manifest.json")
    return {
        contract_id: {subquery.id: subquery.aliases for subquery in contract.subqueries}
        for contract_id, contract in manifest.contracts.items()
        if contract_id.startswith(prefix)
    }


def test_manifest_covers_rq_and_hq_contracts_and_three_suites() -> None:
    manifest = load_manifest(PROJECT_ROOT / "queries" / "evaluation" / "manifest.json")

    assert set(manifest.contracts) == {
        *(f"RQ{number:02d}" for number in range(1, 21)),
        *(f"HQ{number:02d}" for number in range(1, 11)),
    }
    assert sum(case.suite == "canonical" for case in manifest.cases) == 20
    assert sum(case.suite == "robustness" for case in manifest.cases) == 60
    assert sum(case.suite == "complexity" for case in manifest.cases) == 10
    assert all(contract.subqueries for contract in manifest.contracts.values())
    assert (
        "active_vendor_count"
        in manifest.contracts["RQ03"].subqueries[0].aliases["activeSupplierCount"]
    )


def test_canonical_rq_contracts_cases_and_gold_are_frozen() -> None:
    manifest_path = PROJECT_ROOT / "queries" / "evaluation" / "manifest.json"
    raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    contracts = [item for item in raw["contracts"] if item["id"].startswith("RQ")]
    cases = [item for item in raw["cases"] if item["suite"] == "canonical"]
    gold = [
        (
            f"queries/evaluation/{subquery['gold']}",
            (manifest_path.parent / subquery["gold"]).read_text(encoding="utf-8"),
        )
        for contract in contracts
        for subquery in contract["subqueries"]
    ]

    assert _digest(contracts) == (
        "a11b8920ae6869d7de8b9d418724a808e45f26a4ad2bb8a35a825365bec29c30"
    )
    assert _digest(cases) == (
        "886a90caa937626181f7cf4af8bb498c4cfba16aface24c6aece6e442896dd77"
    )
    assert _digest(gold) == (
        "2df6294af3a6061966bf4a31ae932e2f25ff3540bd9c826a0d64aa67c13ce9bb"
    )
    assert _digest(_effective_aliases("RQ")) == (
        "58fb87d954acbff60bb10b7389924a1fbbdc6a4d8ba06fbebd4699daa369257b"
    )


def test_complexity_contracts_cases_and_gold_are_frozen() -> None:
    manifest_path = PROJECT_ROOT / "queries" / "evaluation" / "manifest.json"
    raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    contracts = [item for item in raw["contracts"] if item["id"].startswith("HQ")]
    cases = [item for item in raw["cases"] if item["suite"] == "complexity"]
    gold = [
        (
            f"queries/evaluation/{subquery['gold']}",
            (manifest_path.parent / subquery["gold"]).read_text(encoding="utf-8"),
        )
        for contract in contracts
        for subquery in contract["subqueries"]
    ]

    assert _digest(contracts) == (
        "9a380efee9ef5ab1786a7e3abc9070673daf5fd39eadc3f9664c7194726e87d9"
    )
    assert _digest(cases) == (
        "493999f006f66d619f2aa1a096fdad019d9f132e866ee2648de4b315e4b59b06"
    )
    assert _digest(gold) == (
        "6b319499112956e7628f07800a545cf83ef5ae10b5ba1f8f4b546ece9e81d046"
    )
    assert _digest(_effective_aliases("HQ")) == (
        "2794f34c0a616b41683f75b60a353b9a383753f00b12c80fa74a8a9a92d1be9e"
    )


def test_robustness_has_scr_variants_with_canonical_parameters() -> None:
    manifest = load_manifest(PROJECT_ROOT / "queries" / "evaluation" / "manifest.json")
    canonical = {
        case.contract_id: case for case in manifest.cases if case.suite == "canonical"
    }
    variants: dict[str, set[str]] = defaultdict(set)

    for case in (item for item in manifest.cases if item.suite == "robustness"):
        match = re.fullmatch(r"RB(\d{2})-([SCR])", case.case_id)
        assert match is not None
        contract_id = f"RQ{match.group(1)}"
        assert case.contract_id == contract_id
        assert case.parameters == canonical[contract_id].parameters
        variants[contract_id].add(match.group(2))

    assert variants == {f"RQ{number:02d}": {"S", "C", "R"} for number in range(1, 21)}


def test_manifest_questions_are_nonempty_and_unique() -> None:
    manifest = load_manifest(PROJECT_ROOT / "queries" / "evaluation" / "manifest.json")
    questions = [case.question.strip() for case in manifest.cases]

    assert all(questions)
    assert not [question for question, count in Counter(questions).items() if count > 1]


def test_complexity_route_and_support_distribution_is_locked() -> None:
    manifest = load_manifest(PROJECT_ROOT / "queries" / "evaluation" / "manifest.json")
    contracts = [manifest.contracts[f"HQ{number:02d}"] for number in range(1, 11)]

    assert Counter(contract.route for contract in contracts) == {
        "SQL": 6,
        "GRAPH": 2,
        "HYBRID": 2,
    }
    assert Counter(contract.support_status for contract in contracts) == {
        "FULLY_EVALUATED": 10,
    }
    assert manifest.contracts["HQ01"].expected_entities == [
        {"productId": 771, "productName": "Mountain-100 Silver, 38"},
        {"productId": 775, "productName": "Mountain-100 Black, 38"},
    ]
    assert manifest.contracts["HQ06"].expected_entities == {
        "productId": 680,
        "productName": "HL Road Frame - Black, 58",
    }
    assert manifest.contracts["HQ09"].expected_entities == {
        "productId": 680,
        "productName": "HL Road Frame - Black, 58",
    }
    assert manifest.contracts["HQ08"].expected_entities == {
        "scrapReasonId": 13,
        "scrapReasonName": "Thermoform temperature too low",
    }
    assert manifest.contracts["HQ10"].expected_entities == {
        "locationId": 10,
        "locationName": "Frame Forming",
    }


def test_hybrid_contracts_have_frozen_final_results() -> None:
    manifest = load_manifest(PROJECT_ROOT / "queries" / "evaluation" / "manifest.json")

    for contract_id in ("RQ18", "RQ19", "RQ20", "HQ09", "HQ10"):
        contract = manifest.contracts[contract_id]
        assert contract.support_status == "FULLY_EVALUATED"
        assert {subquery.tool for subquery in contract.subqueries} == {"sql", "graph"}
        assert contract.final_result is not None

    assert manifest.contracts["RQ18"].final_result.row_count == 97  # type: ignore[union-attr]
    assert manifest.contracts["RQ19"].final_result.transform == "bom_shortage_v1"  # type: ignore[union-attr]
    assert manifest.contracts["RQ20"].final_result.row_count == 2  # type: ignore[union-attr]

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
        assert contract.support_status == "FULLY_EVALUATED"
        assert len(contract.subqueries) == (1 if number <= 17 else 2)


def test_gold_parameters_are_available_from_case_or_upstream_binding() -> None:
    manifest = load_manifest(PROJECT_ROOT / "queries" / "evaluation" / "manifest.json")
    primary_cases = {
        case.contract_id: case
        for case in manifest.cases
        if case.suite in {"canonical", "complexity"}
    }

    for contract_id, contract in manifest.contracts.items():
        case = primary_cases[contract_id]
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
        "HQ09": [
            ("graph", (), ("componentId",), {}),
            (
                "sql",
                ("graph_leaf_components",),
                ("componentId",),
                {"componentIds": "graph_leaf_components.componentId"},
            ),
        ],
        "HQ10": [
            ("graph", (), ("productId",), {}),
            (
                "sql",
                ("graph_location_products",),
                ("productId",),
                {"productIds": "graph_location_products.productId"},
            ),
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


def test_final_gold_files_are_frozen_and_not_referenced_by_production_code() -> None:
    manifest_path = PROJECT_ROOT / "queries" / "evaluation" / "manifest.json"
    raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    final_gold = [
        (
            f"queries/evaluation/{contract['finalResult']['gold']}",
            (manifest_path.parent / contract["finalResult"]["gold"]).read_text(
                encoding="utf-8"
            ),
        )
        for contract in raw["contracts"]
        if "finalResult" in contract
    ]

    assert _digest(final_gold) == (
        "ed621774baa5e6249ca0eb4d26cd134e787e5ef242ad25c1706bda15354a747d"
    )
    production_files = [
        path
        for directory in (
            PROJECT_ROOT / "backend/orchestrator",
            PROJECT_ROOT / "backend/api",
        )
        for path in directory.rglob("*.py")
    ]
    production_text = "\n".join(
        path.read_text(encoding="utf-8") for path in production_files
    )
    assert "queries/evaluation" not in production_text
    assert "gold/final" not in production_text


def test_evaluation_manifest_stays_aligned_with_query_contracts() -> None:
    manifest = load_manifest(PROJECT_ROOT / "queries" / "evaluation" / "manifest.json")
    source = json.loads(
        (PROJECT_ROOT / "queries" / "query_contracts.json").read_text(encoding="utf-8")
    )
    source_by_id = {item["id"]: item for item in source["questions"]}
    canonical = {
        case.contract_id: case for case in manifest.cases if case.suite == "canonical"
    }

    for contract_id in (f"RQ{number:02d}" for number in range(1, 21)):
        contract = manifest.contracts[contract_id]
        source_contract = source_by_id[contract_id]
        assert source_contract["route"] == contract.route
        assert source_contract["sampleQuestion"] == canonical[contract_id].question
        if contract.route != "HYBRID":
            assert set(source_contract["requiredAnswerFields"]) == set(
                contract.subqueries[0].required_outputs
            )
