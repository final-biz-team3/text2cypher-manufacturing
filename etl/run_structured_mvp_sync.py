"""구조화 MVP 전체 동기화 진입점.

실행 순서(docs/etl/2-structured_mvp_loading_rules.md 2절 기반, PR #16 리뷰 P1-1
반영으로 검증을 prune 앞뒤로 나눔):
제약조건 적용 -> syncRunId 생성 -> 노드 6종 적재 -> 관계 6종 적재
(적재 전 참조 무결성 검사 포함) -> 사전 검증(이번 syncRunId 범위만) -> prune ->
사후 재검증(전역, 최종 상태 확인).

사전 검증을 prune 앞에 두는 이유: 기존에는 검증이 syncRunId로 범위를 제한하지
않아서, 이번 실행에서 뭔가 누락돼도 이전 실행의 stale 데이터가 검증을 통과시킬
수 있었다. 그 상태에서 prune이 stale 데이터를 지우면 "검증은 통과했지만 실제로는
누락된" 최종 상태가 남는다. 그래서 prune 전에는 이번 syncRunId 범위만 검증해서
적재 자체가 정상인지 확인하고, prune이 끝난 뒤에 전역 범위로 다시 검증해서
최종 상태까지 확인한다.

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
    try:
        driver.verify_connectivity()
    except Exception as exc:
        sys.exit(f"Neo4j 접속 실패 ({os.environ['NEO4J_URI']}): {exc}")

    sync_run_id = generate_sync_run_id()
    print(f"syncRunId = {sync_run_id}")

    try:
        print("1) 제약조건 적용")
        apply_constraints(driver, CONSTRAINTS_PATH)

        print("2) 노드 6종 추출 + 적재")
        node_id_sets: dict[str, set] = {}
        expected_node_counts: dict[str, int] = {}
        for spec in NODE_SPECS:
            raw_rows = extract_rows(pg_conn, spec.extract_sql)
            rows = [
                normalize_row(r, datetime_columns=spec.datetime_columns)
                for r in raw_rows
            ]
            if not rows:
                sys.exit(
                    f"{spec.label} 추출 결과가 0건입니다 - Postgres 연결/쿼리 "
                    "문제일 수 있어 적재를 중단합니다."
                )
            load_rows(driver, spec, rows, sync_run_id)
            node_id_sets[spec.label] = {row[spec.unique_key] for row in rows}
            expected_node_counts[spec.label] = len(rows)
            print(f"   {spec.label}: {len(rows)}건")

        print("3) 관계 6종 추출 + 사전 참조 무결성 검사 + 적재")
        expected_rel_counts: dict[str, int] = {}
        for spec in RELATIONSHIP_SPECS:
            from_key, to_key, from_label, to_label = RELATIONSHIP_ENDPOINTS[
                spec.rel_type
            ]
            raw_rows = extract_rows(pg_conn, spec.extract_sql)
            rows = [normalize_row(r, date_columns=spec.date_columns) for r in raw_rows]
            if not rows:
                sys.exit(
                    f"{spec.rel_type} 추출 결과가 0건입니다 - Postgres 연결/쿼리 "
                    "문제일 수 있어 적재를 중단합니다."
                )

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
            expected_rel_counts[spec.rel_type] = len(rows)
            print(f"   {spec.rel_type}: {len(rows)}건")

        print("4) 사전 검증 (prune 전, 이번 syncRunId 범위만)")
        scoped_node_counts = count_nodes_by_label(
            driver, BUSINESS_LABELS, sync_run_id=sync_run_id
        )
        scoped_rel_counts = count_relationships_by_type(
            driver, RELATIONSHIP_TYPES, sync_run_id=sync_run_id
        )
        if scoped_node_counts != expected_node_counts:
            sys.exit(
                f"노드 적재 건수 불일치 (추출 {expected_node_counts} vs 적재 "
                f"{scoped_node_counts}) - prune 실행 안 함"
            )
        if scoped_rel_counts != expected_rel_counts:
            sys.exit(
                f"관계 적재 건수 불일치 (추출 {expected_rel_counts} vs 적재 "
                f"{scoped_rel_counts}) - prune 실행 안 함"
            )

        parameters_path = ROOT_DIR / "queries" / "query_parameters.json"
        entities = json.loads(parameters_path.read_text(encoding="utf-8"))["entities"]
        failures = verify_fixture_entities(driver, entities, sync_run_id=sync_run_id)
        failures += verify_work_order_17747_fixture(driver, sync_run_id=sync_run_id)
        failures += verify_bom_680_to_492_quantity(driver, sync_run_id=sync_run_id)
        if failures:
            print(f"   사전 fixture 검증 실패 {len(failures)}건 (prune 실행 안 함):")
            for failure in failures:
                print(f"     - {failure}")
            sys.exit(1)
        print("   사전 검증 통과 (건수 일치 + fixture 전부 이번 실행 데이터로 확인)")

        print("5) stale 데이터 prune")
        prune_stale(driver, sync_run_id)

        print("6) 사후 재검증 (prune 이후 최종 상태)")
        final_node_counts = count_nodes_by_label(driver, BUSINESS_LABELS)
        final_rel_counts = count_relationships_by_type(driver, RELATIONSHIP_TYPES)
        print(f"   노드 건수: {final_node_counts}")
        print(f"   관계 건수: {final_rel_counts}")
        failures = verify_fixture_entities(driver, entities)
        failures += verify_work_order_17747_fixture(driver)
        failures += verify_bom_680_to_492_quantity(driver)
        if failures:
            print(f"   사후 fixture 검증 실패 {len(failures)}건 (prune은 이미 실행됨):")
            for failure in failures:
                print(f"     - {failure}")
            sys.exit(1)
        print("   사후 검증 전부 통과")

        print("동기화 완료")
    finally:
        pg_conn.close()
        driver.close()


if __name__ == "__main__":
    main()
