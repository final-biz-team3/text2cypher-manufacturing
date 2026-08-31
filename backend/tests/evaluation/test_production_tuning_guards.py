import re
from pathlib import Path

from evaluation.models import load_manifest

PROJECT_ROOT = Path(__file__).resolve().parents[3]
PRODUCTION_DIRS = ("agents", "api", "orchestrator")


def _production_text() -> str:
    paths = [
        path
        for name in PRODUCTION_DIRS
        for path in (PROJECT_ROOT / "backend" / name).rglob("*.py")
    ]
    return "\n".join(path.read_text(encoding="utf-8") for path in paths)


def test_production_has_no_evaluation_ids_questions_or_imports() -> None:
    text = _production_text()
    manifest = load_manifest(PROJECT_ROOT / "queries" / "evaluation" / "manifest.json")

    assert re.search(r"\b(?:RQ|HQ)\d{2}\b", text) is None
    assert "queries/evaluation" not in text
    assert "evaluation.manifest" not in text
    assert "gold/" not in text.casefold()
    assert not [case.case_id for case in manifest.cases if case.question in text]
    assert not [
        subquery.id
        for contract in manifest.contracts.values()
        for subquery in contract.subqueries
        if subquery.question in text
    ]


def test_production_has_no_known_fixture_identity_literals() -> None:
    text = _production_text()
    manifest = load_manifest(PROJECT_ROOT / "queries" / "evaluation" / "manifest.json")
    entities = [
        entity
        for contract in manifest.contracts.values()
        for entity in (
            contract.expected_entities
            if isinstance(contract.expected_entities, list)
            else [contract.expected_entities]
        )
        if isinstance(entity, dict)
    ]

    violations: list[str] = []
    for entity in entities:
        for field in ("productId", "supplierId", "workOrderId"):
            value = entity.get(field)
            if isinstance(value, int) and re.search(
                rf'["\']?{field}["\']?\s*[:=]\s*{value}\b', text
            ):
                violations.append(f"{field}={value}")
    assert violations == []


def test_output_alias_registry_is_independent_of_evaluation_contracts() -> None:
    source = (
        PROJECT_ROOT / "backend" / "orchestrator" / "output_catalog.py"
    ).read_text(encoding="utf-8")

    assert "evaluation" not in source
    assert "manifest" not in source
    assert re.search(r"\b(?:RQ|HQ)\d{2}\b", source) is None
