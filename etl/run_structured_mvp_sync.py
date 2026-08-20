"""구조화 MVP 전체 동기화 진입점.

실행 순서(docs/design/2-structured_mvp_loading_rules.md 2절 그대로):
제약조건 적용 -> syncRunId 생성 -> 노드 6종 적재 -> 관계 6종 적재
(적재 전 참조 무결성 검사 포함) -> 사후 검증 -> prune.

사용법(리포 루트 기준):
    python etl/run_structured_mvp_sync.py

.env에 POSTGRES_HOST/PORT/DB/USER, NEO4J_URI/USER/PASSWORD가 이미
있어야 한다(자동 로드, 기본값 없음 - 로컬 DB가 있다고 가정하지 않는다).
"""

import json
import os
import sys
import uuid
from datetime import UTC, datetime

import psycopg2
from neo4j import GraphDatabase
from postgres_restore import REQUIRED_ENV_VARS, ROOT_DIR, target_database_exists
from structured_mvp_extract import extract_rows, normalize_row
from structured_mvp_load import (
    BUSINESS_LABELS,
    apply_constraints,
    load_rows,
    prune_stale,
)
from structured_mvp_spec import NODE_SPECS, RELATIONSHIP_SPECS
from structured_mvp_validate import (
    count_nodes_by_label,
    count_relationships_by_type,
    find_dangling_relationship_rows,
    verify_bom_680_to_492_quantity,
    verify_fixture_entities,
    verify_work_order_17747_fixture,
)

CONSTRAINTS_PATH = ROOT_DIR / "schema" / "structured_mvp_constraints.cypher"
RELATIONSHIP_TYPES = [spec.rel_type for spec in RELATIONSHIP_SPECS]
NEO4J_REQUIRED_ENV_VARS = ["NEO4J_URI", "NEO4J_USER", "NEO4J_PASSWORD"]

# 관계별 (시작 컬럼, 도착 컬럼, 시작 라벨, 도착 라벨) - 사전 참조 무결성 검사용
RELATIONSHIP_ENDPOINTS = {
    "SUPPLIES": ("supplierId", "productId", "Supplier", "Product"),
    "REQUIRES_COMPONENT": (
        "assemblyProductId",
        "componentProductId",
        "Product",
        "Product",
    ),
    "PRODUCES": ("workOrderId", "productId", "WorkOrder", "Product"),
    "HAS_OPERATION": (
        "workOrderId",
        "routingOperationKey",
        "WorkOrder",
        "RoutingOperation",
    ),
    "PERFORMED_AT": (
        "routingOperationKey",
        "locationId",
        "RoutingOperation",
        "Location",
    ),
    "SCRAPPED_DUE_TO": ("workOrderId", "scrapReasonId", "WorkOrder", "ScrapReason"),
}


def generate_sync_run_id() -> str:
    return f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"


def main() -> None:
    from dotenv import load_dotenv

    load_dotenv(ROOT_DIR / ".env")

    missing_vars = [
        name
        for name in [*REQUIRED_ENV_VARS, *NEO4J_REQUIRED_ENV_VARS]
        if not os.environ.get(name)
    ]
    if missing_vars:
        sys.exit(f".env에 다음 값이 없습니다: {', '.join(missing_vars)}")

    pg_host = os.environ["POSTGRES_HOST"]
    pg_port = os.environ["POSTGRES_PORT"]
    pg_db = os.environ["POSTGRES_DB"]
    pg_user = os.environ["POSTGRES_USER"]
    pg_password = os.environ.get("POSTGRES_PASSWORD", "")
    print(f"PostgreSQL 대상: {pg_host}:{pg_port}/{pg_db}")
    print(f"Neo4j 대상: {os.environ['NEO4J_URI']}")

    pg_conn = psycopg2.connect(
        host=pg_host, port=pg_port, dbname=pg_db, user=pg_user, password=pg_password
    )
    if not target_database_exists(pg_conn, pg_db):
        sys.exit(f"'{pg_db}' 데이터베이스가 {pg_host}:{pg_port}에 없습니다.")

    driver = GraphDatabase.driver(
        os.environ["NEO4J_URI"],
        auth=(os.environ["NEO4J_USER"], os.environ["NEO4J_PASSWORD"]),
    )

    sync_run_id = generate_sync_run_id()
    print(f"syncRunId = {sync_run_id}")

    try:
        print("1) 제약조건 적용")
        apply_constraints(driver, CONSTRAINTS_PATH)

        print("2) 노드 6종 추출 + 적재")
        node_id_sets: dict[str, set] = {}
        for spec in NODE_SPECS:
            raw_rows = extract_rows(pg_conn, spec.extract_sql)
            rows = [
                normalize_row(r, datetime_columns=spec.datetime_columns)
                for r in raw_rows
            ]
            load_rows(driver, spec, rows, sync_run_id)
            node_id_sets[spec.label] = {row[spec.unique_key] for row in rows}
            print(f"   {spec.label}: {len(rows)}건")

        print("3) 관계 6종 추출 + 사전 참조 무결성 검사 + 적재")
        for spec in RELATIONSHIP_SPECS:
            from_key, to_key, from_label, to_label = RELATIONSHIP_ENDPOINTS[
                spec.rel_type
            ]
            raw_rows = extract_rows(pg_conn, spec.extract_sql)
            rows = [normalize_row(r, date_columns=spec.date_columns) for r in raw_rows]

            dangling = find_dangling_relationship_rows(
                rows,
                from_key=from_key,
                to_key=to_key,
                from_ids=node_id_sets[from_label],
                to_ids=node_id_sets[to_label],
            )
            if dangling:
                sys.exit(
                    f"{spec.rel_type}: 참조 누락 {len(dangling)}건 -> {dangling[:5]}"
                )

            load_rows(driver, spec, rows, sync_run_id)
            print(f"   {spec.rel_type}: {len(rows)}건")

        print("4) 사후 검증")
        node_counts = count_nodes_by_label(driver, BUSINESS_LABELS)
        rel_counts = count_relationships_by_type(driver, RELATIONSHIP_TYPES)
        print(f"   노드 건수: {node_counts}")
        print(f"   관계 건수: {rel_counts}")

        parameters_path = ROOT_DIR / "queries" / "query_parameters.json"
        entities = json.loads(parameters_path.read_text(encoding="utf-8"))["entities"]
        failures = verify_fixture_entities(driver, entities)
        failures += verify_work_order_17747_fixture(driver)
        failures += verify_bom_680_to_492_quantity(driver)
        if failures:
            print(f"   fixture 검증 실패 {len(failures)}건:")
            for failure in failures:
                print(f"     - {failure}")
            sys.exit(1)
        print("   fixture 검증 전부 통과")

        print("5) stale 데이터 prune")
        prune_stale(driver, sync_run_id)

        print("동기화 완료")
    finally:
        pg_conn.close()
        driver.close()


if __name__ == "__main__":
    main()
