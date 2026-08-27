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


def test_all_holdout_gold_queries_match_the_approved_snapshot() -> None:
    load_dotenv(PROJECT_ROOT / ".env")
    manifest = load_manifest(PROJECT_ROOT / "queries" / "evaluation" / "manifest.json")
    cases = [case for case in manifest.cases if case.suite == "holdout"]
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

    assert len(result.records) == 10
    assert all(record["status"] == "GOLD_VALIDATED" for record in result.records)
    subqueries = {
        (record["caseId"], subquery["id"]): subquery
        for record in result.records
        for subquery in record["subqueries"]
    }
    assert len(subqueries) == 12
    assert all(subquery["status"] == "PASS" for subquery in subqueries.values())
    assert {
        key: (subquery["rowCount"], subquery["goldHash"])
        for key, subquery in subqueries.items()
    } == {
        ("HQ01", "sql_price_cost_gap"): (
            2,
            "ef0f40b2a2b0914c22532f3f4e82a1bc00c88a84f932b1bdb0d64b6494d564f4",
        ),
        ("HQ02", "sql_top_inventory_shortages"): (
            5,
            "7fa9d5660c139a91948e720caf597780faeed79ee265d7b22ad8d6ae50a5dad0",
        ),
        ("HQ03", "sql_top_active_suppliers"): (
            5,
            "e40ad8a2197e588e1ba51970eb41bbaed495db2d35118c9a15f97391e557a518",
        ),
        ("HQ04", "sql_category_average_price"): (
            4,
            "87e313db8f977a547f3f9e5af88a5a1f953a88cf205122ce91a26be7ff1bf0eb",
        ),
        ("HQ05", "sql_top_work_order_locations"): (
            5,
            "1669b3c4557c094f3542df652fc042d305347049b7b38d28145fcd26cc9de660",
        ),
        ("HQ06", "graph_leaf_components"): (
            10,
            "f484dc39671ce1525778d76519a9fbd11f3b0afca08b9c6ffe7729c23159e292",
        ),
        ("HQ07", "graph_supplier_pairs"): (
            5,
            "9e18f50ad67835cbc77c26bd3cd58ed3f4b528514065992e578d6ba079b9f857",
        ),
        ("HQ08", "sql_top_scrapped_products"): (
            5,
            "dc0185121c4bdea60893cd4a2490fc943f819b046353d95094b2a9bc8fbf56af",
        ),
        ("HQ09", "graph_leaf_components"): (
            10,
            "f484dc39671ce1525778d76519a9fbd11f3b0afca08b9c6ffe7729c23159e292",
        ),
        ("HQ09", "sql_leaf_shortages"): (
            5,
            "b9f5bf01a328175269cee0c9e458155e96807f71a03f6d928dcfd6c4474d4253",
        ),
        ("HQ10", "graph_location_products"): (
            19,
            "a1db21aa47a3ef36f55979ef447b6d3697e7b2fe701465711d9f6fa8711adc0b",
        ),
        ("HQ10", "sql_product_scrap_totals"): (
            5,
            "5a168bb30431ee60a166d40a8b7c5639ef66cf8dc08feb30bad443ec99d64ea4",
        ),
    }
