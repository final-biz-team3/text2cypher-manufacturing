from pathlib import Path
from typing import Any

import pytest
from dotenv import load_dotenv

from evaluation.contracts import collect_input_bindings
from evaluation.database import ReadOnlyDatabaseExecutor
from evaluation.models import EvaluationManifest, load_manifest
from evaluation.runner import EvaluationRunner

pytestmark = pytest.mark.integration

PROJECT_ROOT = Path(__file__).resolve().parents[3]


def _holdout_gold_rows(
    runner: EvaluationRunner, manifest: EvaluationManifest
) -> dict[tuple[str, str], list[dict[str, Any]]]:
    """의존 binding까지 적용해 Holdout Gold의 정규화 결과를 반환한다."""
    outputs: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for case in (case for case in manifest.cases if case.suite == "holdout"):
        upstream: dict[str, list[dict[str, Any]]] = {}
        contract = manifest.contracts[case.contract_id]
        for expected in contract.subqueries:
            inputs = collect_input_bindings(expected.input_bindings, upstream)
            parameters = {**case.parameters, **inputs}
            rows, _ = runner._gold_result(expected, parameters)
            upstream[expected.id] = rows
            outputs[(case.case_id, expected.id)] = rows
    return outputs


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
    subqueries = {
        (record["caseId"], subquery["id"]): subquery
        for record in result.records
        for subquery in record["subqueries"]
    }
    assert len(subqueries) == 23
    assert all(subquery["status"] == "PASS" for subquery in subqueries.values())
    assert {
        key: (subquery["rowCount"], subquery["goldHash"])
        for key, subquery in subqueries.items()
    } == {
        ("RQ01", "sql_product_cost"): (
            1,
            "492d30c40b039ecaf38d4eb1717a6eefe88616e512e8f04802b4880d8f5f0a88",
        ),
        ("RQ02", "sql_inventory_locations"): (
            6,
            "ef9fb77bfadfa60cdcdf13fbdec3369e9e4a42dc89b6241333c26f5b7365ecd4",
        ),
        ("RQ03", "sql_active_supplier_count"): (
            1,
            "63358d84119d2025ca5bb079f2be364b7265d478a1f7a43147c8219f58c9383c",
        ),
        ("RQ04", "sql_purchased_product_count"): (
            1,
            "53f441930a223f121b0170e8dfa12eccdb711acf8a87afba0132fbfadcf5eeb2",
        ),
        ("RQ05", "sql_products_without_sell_end"): (
            10,
            "030ebe38da1b884ffe099500606405c536281d8cf27b31a59cf49b2fd783f222",
        ),
        ("RQ06", "sql_product_attributes"): (
            1,
            "a70650d88b5e2c871a7c4cdc110c277ba1389afae289d7d0c24a7bd2d43ca302",
        ),
        ("RQ07", "sql_category_product_count"): (
            1,
            "ddb1457f018753a879e6f09f4967ae33090de886f72787ebc82e4f86e4a0e3fc",
        ),
        ("RQ08", "sql_stock_shortage"): (
            1,
            "9f8470d3238421e137a0c21890d675439070b419e0422168ebf5bffe06dee76b",
        ),
        ("RQ09", "sql_top_finished_sales"): (
            5,
            "aebf47dbfcdb0c15b8baefc2ab341cae823fb2d88cc275a17f457795bd7cf606",
        ),
        ("RQ10", "sql_top_supplier_rejections"): (
            5,
            "ddbafe0ffd41dcba6c433acead4cb9a7833e739418bf0b03eddaf17c9bbd23d0",
        ),
        ("RQ11", "sql_top_scrapped_work_orders"): (
            5,
            "d309c768b65f67a03d1141c616be2edf59192e871f55cf384452c1a1572f367d",
        ),
        ("RQ12", "graph_component_usage"): (
            54,
            "526def0635da674100aa090a99da509dc3d6949bcf190debbc239eb3e823bac2",
        ),
        ("RQ13", "graph_bom_hierarchy"): (
            24,
            "4673abc9700cd9e3b658c6e09bc6ec27cba0d4f11d4070c073186fdb68ba0b82",
        ),
        ("RQ14", "graph_supplier_impact"): (
            97,
            "18d1bc44e354dc4049f24d39588a5d0c5b460f6357807acb4ed50541590c9d22",
        ),
        ("RQ15", "graph_work_order_operations"): (
            2,
            "e3304588163ebc4c1b8698e3793781aa8ccde97f95b5e5ba164e5238de19d529",
        ),
        ("RQ16", "graph_all_bom_paths"): (
            3,
            "ec0c20056b97a19544d0942dca3949e2256fdec2df62a0cebdedd50f1ad2dad6",
        ),
        ("RQ17", "graph_common_components"): (
            53,
            "b57c42fa56de77dd096adc0721f31fb4ae188828299474be00d914632f8f207c",
        ),
        ("RQ18", "graph_impact"): (
            97,
            "18d1bc44e354dc4049f24d39588a5d0c5b460f6357807acb4ed50541590c9d22",
        ),
        ("RQ18", "sql_stock"): (
            1,
            "99f30ec09a0a82969da6545206b5ece94b6d242b1a7b4123f5092f100de7e052",
        ),
        ("RQ19", "graph_bom_supply"): (
            25,
            "78c01a408ca1aaff9998cb04fee6159d6330ebe587a7596b6d19592c58cfe9bc",
        ),
        ("RQ19", "sql_component_stock"): (
            21,
            "d51df7e73440f149edf2d122264c871fd843c6cdd1c5e1de601579f8c730e391",
        ),
        ("RQ20", "sql_scrap_facts"): (
            1,
            "c681cd77797449a292622539029adc9286888713cc03d66faa2fff5cf3386722",
        ),
        ("RQ20", "graph_operations"): (
            2,
            "e3304588163ebc4c1b8698e3793781aa8ccde97f95b5e5ba164e5238de19d529",
        ),
    }


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


def test_holdout_gold_satisfies_independent_business_invariants() -> None:
    """Gold hash와 다른 축에서 집계 grain·BOM leaf·source 동기화를 검증한다."""
    load_dotenv(PROJECT_ROOT / ".env")
    manifest = load_manifest(PROJECT_ROOT / "queries" / "evaluation" / "manifest.json")
    cases = {case.case_id: case for case in manifest.cases if case.suite == "holdout"}
    database = ReadOnlyDatabaseExecutor.from_environment()
    try:
        runner = EvaluationRunner(
            manifest,
            database,
            None,
            project_root=PROJECT_ROOT,
        )
        outputs = _holdout_gold_rows(runner, manifest)

        shortages = outputs[("HQ02", "sql_top_inventory_shortages")]
        shortage_facts = database.execute_sql(
            """
            SELECT p.productid AS "productId",
                   p.safetystocklevel AS "safetyStockLevel",
                   COALESCE((
                     SELECT SUM(i.quantity)
                     FROM production.productinventory AS i
                     WHERE i.productid = p.productid
                   ), 0) AS "actualStock"
            FROM production.product AS p
            WHERE p.productid = ANY(%(productIds)s)
            """,
            {"productIds": [row["productId"] for row in shortages]},
            max_rows=5,
        )
        shortage_facts_by_id = {row["productId"]: row for row in shortage_facts}
        assert all(
            row["actualStock"] == shortage_facts_by_id[row["productId"]]["actualStock"]
            and row["safetyStockLevel"]
            == shortage_facts_by_id[row["productId"]]["safetyStockLevel"]
            and row["shortageQty"] == row["safetyStockLevel"] - row["actualStock"] > 0
            for row in shortages
        )
        assert [(-row["shortageQty"], row["productId"]) for row in shortages] == sorted(
            (-row["shortageQty"], row["productId"]) for row in shortages
        )

        suppliers = outputs[("HQ03", "sql_top_active_suppliers")]
        supplier_facts = database.execute_sql(
            """
            SELECT v.businessentityid AS "supplierId",
                   v.activeflag AS "active",
                   (
                     SELECT COUNT(DISTINCT pv.productid)
                     FROM purchasing.productvendor AS pv
                     WHERE pv.businessentityid = v.businessentityid
                   ) AS "suppliedProductCount"
            FROM purchasing.vendor AS v
            WHERE v.businessentityid = ANY(%(supplierIds)s)
            """,
            {"supplierIds": [row["supplierId"] for row in suppliers]},
            max_rows=5,
        )
        supplier_facts_by_id = {row["supplierId"]: row for row in supplier_facts}
        assert all(
            supplier_facts_by_id[row["supplierId"]]["active"] is True
            and supplier_facts_by_id[row["supplierId"]]["suppliedProductCount"]
            == row["suppliedProductCount"]
            for row in suppliers
        )

        leaf_rows = outputs[("HQ06", "graph_leaf_components")]
        sql_leaf_rows = database.execute_sql(
            """
            WITH RECURSIVE bom_tree AS (
              SELECT bom.componentid,
                     1 AS depth,
                     ARRAY[root.productid, bom.componentid] AS path
              FROM production.product AS root
              JOIN production.billofmaterials AS bom
                ON bom.productassemblyid = root.productid
              WHERE root.name = %(finishedProductName)s
                AND bom.startdate <= %(bomAsOfDate)s
                AND (bom.enddate IS NULL OR %(bomAsOfDate)s < bom.enddate)

              UNION ALL

              SELECT bom.componentid,
                     tree.depth + 1,
                     tree.path || bom.componentid
              FROM bom_tree AS tree
              JOIN production.billofmaterials AS bom
                ON bom.productassemblyid = tree.componentid
              WHERE tree.depth < 4
                AND bom.startdate <= %(bomAsOfDate)s
                AND (bom.enddate IS NULL OR %(bomAsOfDate)s < bom.enddate)
                AND NOT bom.componentid = ANY(tree.path)
            )
            SELECT tree.componentid AS "componentId",
                   MIN(tree.depth) AS "minDepth"
            FROM bom_tree AS tree
            WHERE NOT EXISTS (
              SELECT 1
              FROM production.billofmaterials AS child
              WHERE child.productassemblyid = tree.componentid
                AND child.startdate <= %(bomAsOfDate)s
                AND (child.enddate IS NULL OR %(bomAsOfDate)s < child.enddate)
            )
            GROUP BY tree.componentid
            ORDER BY "minDepth" DESC, "componentId" ASC
            """,
            cases["HQ06"].parameters,
            max_rows=50,
        )
        assert [(row["componentId"], row["minDepth"]) for row in leaf_rows] == [
            (row["componentId"], row["minDepth"]) for row in sql_leaf_rows
        ]

        supplier_pairs = outputs[("HQ07", "graph_supplier_pairs")]
        sql_supplier_pairs = database.execute_sql(
            """
            SELECT a.businessentityid AS "supplierIdA",
                   b.businessentityid AS "supplierIdB",
                   COUNT(DISTINCT a.productid) AS "sharedComponentCount"
            FROM purchasing.productvendor AS a
            JOIN purchasing.productvendor AS b
              ON b.productid = a.productid
             AND a.businessentityid < b.businessentityid
            JOIN purchasing.vendor AS va
              ON va.businessentityid = a.businessentityid
             AND va.activeflag = true
            JOIN purchasing.vendor AS vb
              ON vb.businessentityid = b.businessentityid
             AND vb.activeflag = true
            GROUP BY a.businessentityid, b.businessentityid
            ORDER BY "sharedComponentCount" DESC,
                     "supplierIdA" ASC,
                     "supplierIdB" ASC
            LIMIT 5
            """,
            {},
            max_rows=5,
        )
        assert [
            (row["supplierIdA"], row["supplierIdB"], row["sharedComponentCount"])
            for row in supplier_pairs
        ] == [
            (row["supplierIdA"], row["supplierIdB"], row["sharedComponentCount"])
            for row in sql_supplier_pairs
        ]
        assert all(row["supplierIdA"] < row["supplierIdB"] for row in supplier_pairs)

        hybrid_leaf_rows = outputs[("HQ09", "graph_leaf_components")]
        leaf_shortages = outputs[("HQ09", "sql_leaf_shortages")]
        assert {(row["componentId"], row["minDepth"]) for row in hybrid_leaf_rows} == {
            (row["componentId"], row["minDepth"]) for row in leaf_rows
        }
        leaf_ids = {row["componentId"] for row in hybrid_leaf_rows}
        assert all(
            row["componentId"] in leaf_ids
            and row["shortageQty"] == row["safetyStockLevel"] - row["actualStock"] > 0
            for row in leaf_shortages
        )

        location_products = outputs[("HQ10", "graph_location_products")]
        sql_location_products = database.execute_sql(
            """
            SELECT DISTINCT w.productid AS "productId"
            FROM production.workorderrouting AS routing
            JOIN production.workorder AS w
              ON w.workorderid = routing.workorderid
            JOIN production.location AS location
              ON location.locationid = routing.locationid
            WHERE location.name = %(locationName)s
            ORDER BY "productId" ASC
            """,
            cases["HQ10"].parameters,
            max_rows=50,
        )
        graph_product_ids = [row["productId"] for row in location_products]
        assert graph_product_ids == [row["productId"] for row in sql_location_products]
        scrap_totals = outputs[("HQ10", "sql_product_scrap_totals")]
        assert all(row["productId"] in graph_product_ids for row in scrap_totals)
        assert [
            (-row["totalScrappedQty"], row["productId"]) for row in scrap_totals
        ] == sorted(
            (-row["totalScrappedQty"], row["productId"]) for row in scrap_totals
        )
    finally:
        database.close()
