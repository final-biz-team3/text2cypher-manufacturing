from pathlib import Path

import pytest
from dotenv import load_dotenv

from evaluation.database import ReadOnlyDatabaseExecutor
from evaluation.models import load_manifest
from evaluation.runner import EvaluationRunner

pytestmark = pytest.mark.integration

PROJECT_ROOT = Path(__file__).resolve().parents[3]


def test_all_canonical_gold_queries_match_the_approved_snapshot() -> None:
    load_dotenv(PROJECT_ROOT / ".env")
    manifest = load_manifest(PROJECT_ROOT / "queries" / "evaluation" / "manifest.json")
    cases = [case for case in manifest.cases if case.suite == "canonical"]
    database = ReadOnlyDatabaseExecutor.from_environment()
    try:
        runner = EvaluationRunner(
            manifest,
            database,
            None,
            project_root=PROJECT_ROOT,
        )
        result = runner.validate_gold(cases)
    finally:
        database.close()

    assert len(result.records) == 20
    assert all(record["status"] == "GOLD_VALIDATED" for record in result.records)
    assert sum(len(record["subqueries"]) for record in result.records) == 23
