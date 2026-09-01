"""구조화 MVP 전체 동기화 진입점.

실행 순서:
1) PostgreSQL에서 노드·관계 6종씩 전부 추출(Neo4j에는 아직 한 글자도 안 씀)
2) 추출 결과만으로 쓰기 전 검증 - 0건 / 필수 키 NULL / 중복 키 / 참조 무결성
   (하나라도 실패하면 여기서 끝, Neo4j는 완전히 그대로다)
3) 새 Neo4j 데이터베이스를 만들어 그 안에만 제약조건 적용 + 전체 적재
4) 새 데이터베이스를 대상으로 적재 후 검증(추출 건수 == 적재 건수, fixture)
5) 통과했을 때만 새 데이터베이스를 기본 데이터베이스로 승격 - 기존 기본
   데이터베이스는 지우지 않고 멈춘 채로 보존한다.

왜 이렇게 설계했나: 스펙 하나를 추출하자마자 바로 라이브 그래프에 적재하는
방식은, 예를 들어 Product·Supplier까지 적재된 뒤 WorkOrder 추출이나 적재가
실패해도 이미 라이브 그래프에는 Product·Supplier의 새 데이터가 커밋돼버린
상태로 남는다(부분 갱신). 지금 구조는 (a) 쓰기 시작 전에 모든 걸 검증해서
로직/데이터 문제로 인한 실패는 Neo4j에 아무 흔적도 안 남기고, (b) 그래도
남는 "쓰기 도중 실패" 위험은 격리된 새 데이터베이스에만 쓰고 검증까지
통과했을 때만 승격하는 방식으로 없앤다 - 실패하면 라이브 데이터베이스는
한 번도 안 건드려진 채로 그대로다. 매 실행이 완전히 새 빈 데이터베이스에서
시작하므로 이전 실행의 stale 데이터를 지우는 별도 로직(prune)도 필요 없다.

사용법(리포 루트 기준):
    python etl/run_structured_mvp_sync.py
    python etl/run_structured_mvp_sync.py --retry-promote <새 DB 이름>  # 승격만 재시도

.env에 POSTGRES_HOST/PORT/DB/USER, NEO4J_URI/USER/PASSWORD가 이미
있어야 한다(자동 로드, 기본값 없음 - 로컬 DB가 있다고 가정하지 않는다).
"""

import argparse
import json
import os
import sys
import uuid
from datetime import UTC, datetime

import psycopg2
import psycopg2.extensions
from neo4j import Driver
from paths import ROOT_DIR
from postgres_restore import REQUIRED_ENV_VARS, target_database_exists
from structured_mvp_config import (
    BUSINESS_LABELS,
    RELATIONSHIP_TYPES,
    connect_neo4j_from_env,
)
from structured_mvp_extract import extract_rows, normalize_row
from structured_mvp_load import (
    apply_constraints,
    create_neo4j_database,
    generate_database_name,
    get_default_database,
    load_rows,
    retry_promote,
)
from structured_mvp_spec import NODE_SPECS, RELATIONSHIP_SPECS
from structured_mvp_validate import (
    count_nodes_by_label,
    count_relationships_by_type,
    find_dangling_relationship_rows,
    find_duplicate_key_rows,
    find_rows_with_null_key,
    verify_bom_680_to_492_quantity,
    verify_fixture_entities,
    verify_work_order_17747_fixture,
)

CONSTRAINTS_PATH = ROOT_DIR / "schema" / "structured_mvp_constraints.cypher"


def generate_sync_run_id() -> str:
    return f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"


def extract_all(
    pg_conn: psycopg2.extensions.connection,
) -> tuple[dict[str, list[dict]], dict[str, list[dict]]]:
    """1단계: NODE_SPECS/RELATIONSHIP_SPECS를 순회해 추출·정규화. (nodes, rels) 반환.

    Neo4j에는 아직 아무것도 쓰지 않는다 - 여기서 실패해도 라이브 그래프는 그대로다.
    """
    extracted_nodes: dict[str, list[dict]] = {}
    for spec in NODE_SPECS:
        raw_rows = extract_rows(pg_conn, spec.extract_sql)
        rows = [
            normalize_row(r, datetime_columns=spec.datetime_columns) for r in raw_rows
        ]
        extracted_nodes[spec.label] = rows
        print(f"   {spec.label}: {len(rows)}건 추출")

    extracted_rels: dict[str, list[dict]] = {}
    for spec in RELATIONSHIP_SPECS:
        raw_rows = extract_rows(pg_conn, spec.extract_sql)
        rows = [normalize_row(r, date_columns=spec.date_columns) for r in raw_rows]
        extracted_rels[spec.rel_type] = rows
        print(f"   {spec.rel_type}: {len(rows)}건 추출")

    return extracted_nodes, extracted_rels


def validate_before_write(
    nodes: dict[str, list[dict]], rels: dict[str, list[dict]]
) -> list[str]:
    """2단계: 0건 / 필수 키 NULL / 중복 키 / 참조 무결성. 위반 메시지 리스트 반환(빈 리스트=통과).

    종료(sys.exit) 결정은 호출자(main)만 한다 - structured_mvp_validate.py의
    fixture 검사(verify_*)가 이미 실패 리스트를 반환하는 규약과 맞춘다. 한 스펙에서
    위반이 나오면 그 스펙의 나머지 검사는 건너뛰고 다음 스펙으로 간다(원래의
    "첫 위반에서 중단" 문구를 그대로 유지하기 위함).
    """
    failures: list[str] = []

    for spec in NODE_SPECS:
        rows = nodes.get(spec.label, [])
        if not rows:
            failures.append(
                f"{spec.label} 추출 결과가 0건입니다 - Postgres 연결/쿼리 "
                "문제일 수 있어 중단합니다(Neo4j는 안 건드림)."
            )
            continue
        null_rows = find_rows_with_null_key(rows, (spec.unique_key,))
        if null_rows:
            failures.append(
                f"{spec.label}: {spec.unique_key}이 NULL인 행 "
                f"{len(null_rows)}건 -> {null_rows[:5]}"
            )
            continue
        duplicates = find_duplicate_key_rows(rows, (spec.unique_key,))
        if duplicates:
            failures.append(
                f"{spec.label}: 중복된 {spec.unique_key} -> {duplicates[:5]}"
            )

    node_id_sets = {
        spec.label: {row[spec.unique_key] for row in nodes.get(spec.label, [])}
        for spec in NODE_SPECS
    }

    for spec in RELATIONSHIP_SPECS:
        rows = rels.get(spec.rel_type, [])
        if not rows:
            failures.append(
                f"{spec.rel_type} 추출 결과가 0건입니다 - Postgres 연결/쿼리 "
                "문제일 수 있어 중단합니다(Neo4j는 안 건드림)."
            )
            continue
        null_rows = find_rows_with_null_key(rows, spec.merge_key_columns)
        if null_rows:
            failures.append(
                f"{spec.rel_type}: {spec.merge_key_columns}이 NULL인 행 "
                f"{len(null_rows)}건 -> {null_rows[:5]}"
            )
            continue
        duplicates = find_duplicate_key_rows(rows, spec.merge_key_columns)
        if duplicates:
            failures.append(
                f"{spec.rel_type}: 중복된 {spec.merge_key_columns} -> {duplicates[:5]}"
            )
            continue
        dangling = find_dangling_relationship_rows(
            rows,
            from_key=spec.from_key,
            to_key=spec.to_key,
            from_ids=node_id_sets[spec.from_label],
            to_ids=node_id_sets[spec.to_label],
        )
        if dangling:
            failures.append(
                f"{spec.rel_type}: 참조 누락 {len(dangling)}건 -> {dangling[:5]}"
            )

    return failures


def load_all(
    driver: Driver,
    database: str,
    nodes: dict[str, list[dict]],
    rels: dict[str, list[dict]],
    sync_run_id: str,
) -> tuple[dict[str, int], dict[str, int]]:
    """4단계: 제약조건 적용 + 배치 적재. (expected_node_counts, expected_rel_counts) 반환.

    항상 격리된 새 데이터베이스를 대상으로 한다 - 라이브 그래프는 승격 전까지
    건드리지 않는다.
    """
    apply_constraints(driver, CONSTRAINTS_PATH, database=database)

    expected_node_counts: dict[str, int] = {}
    for spec in NODE_SPECS:
        rows = nodes[spec.label]
        load_rows(driver, spec, rows, sync_run_id, database=database)
        expected_node_counts[spec.label] = len(rows)
        print(f"   {spec.label}: {len(rows)}건 적재")

    expected_rel_counts: dict[str, int] = {}
    for spec in RELATIONSHIP_SPECS:
        rows = rels[spec.rel_type]
        load_rows(driver, spec, rows, sync_run_id, database=database)
        expected_rel_counts[spec.rel_type] = len(rows)
        print(f"   {spec.rel_type}: {len(rows)}건 적재")

    return expected_node_counts, expected_rel_counts


def validate_after_load(
    driver: Driver,
    database: str,
    expected_node_counts: dict[str, int],
    expected_rel_counts: dict[str, int],
) -> list[str]:
    """5단계: 적재 건수 일치 + fixture 검증. 위반 메시지 리스트 반환(빈 리스트=통과).

    건수가 어긋나면 fixture까지 갈 것 없이 거기서 리스트를 돌려준다(원래 흐름과
    동일). 통과 시 건수 요약 출력은 여기서 한다.
    """
    actual_node_counts = count_nodes_by_label(driver, BUSINESS_LABELS, database=database)
    actual_rel_counts = count_relationships_by_type(
        driver, RELATIONSHIP_TYPES, database=database
    )
    if actual_node_counts != expected_node_counts:
        return [
            f"노드 적재 건수 불일치 (추출 {expected_node_counts} vs 적재 "
            f"{actual_node_counts}) - 새 데이터베이스 '{database}'는 조사를 위해 "
            "남겨뒀습니다. 기존 기본 데이터베이스는 승격하지 않아 안전합니다."
        ]
    if actual_rel_counts != expected_rel_counts:
        return [
            f"관계 적재 건수 불일치 (추출 {expected_rel_counts} vs 적재 "
            f"{actual_rel_counts}) - 새 데이터베이스 '{database}'는 조사를 위해 "
            "남겨뒀습니다. 기존 기본 데이터베이스는 승격하지 않아 안전합니다."
        ]

    parameters_path = ROOT_DIR / "queries" / "query_parameters.json"
    entities = json.loads(parameters_path.read_text(encoding="utf-8"))["entities"]
    failures = verify_fixture_entities(driver, entities, database=database)
    failures += verify_work_order_17747_fixture(driver, database=database)
    failures += verify_bom_680_to_492_quantity(driver, database=database)
    if failures:
        return failures

    print(f"   노드 건수: {actual_node_counts}")
    print(f"   관계 건수: {actual_rel_counts}")
    print("   적재 후 검증 전부 통과")
    return []


def main() -> None:
    from dotenv import load_dotenv

    load_dotenv(ROOT_DIR / ".env")

    parser = argparse.ArgumentParser(
        description="구조화 MVP를 PostgreSQL에서 Neo4j로 동기화한다."
    )
    parser.add_argument(
        "--retry-promote",
        metavar="NEW_DB_NAME",
        help=(
            "적재·검증까지는 끝났지만 승격(마지막 단계)만 실패한 경우, 처음부터 "
            "다시 추출·적재하지 않고 이 단계만 재시도한다. 값은 이미 존재하는 "
            "새 Neo4j 데이터베이스 이름(예: mvpgraph-20260821t090349z)."
        ),
    )
    args = parser.parse_args()

    driver = connect_neo4j_from_env()
    print(f"Neo4j 대상: {os.environ['NEO4J_URI']}")

    if args.retry_promote:
        print(f"승격 재시도 전용 모드: '{args.retry_promote}' -> 기본 데이터베이스")
        try:
            retry_promote(driver, args.retry_promote)
        finally:
            driver.close()
        return

    missing_pg_vars = [name for name in REQUIRED_ENV_VARS if not os.environ.get(name)]
    if missing_pg_vars:
        driver.close()
        sys.exit(f".env에 다음 값이 없습니다: {', '.join(missing_pg_vars)}")

    pg_host = os.environ["POSTGRES_HOST"]
    pg_port = os.environ["POSTGRES_PORT"]
    pg_db = os.environ["POSTGRES_DB"]
    pg_user = os.environ["POSTGRES_USER"]
    pg_password = os.environ.get("POSTGRES_PASSWORD", "")
    print(f"PostgreSQL 대상: {pg_host}:{pg_port}/{pg_db}")

    pg_conn = psycopg2.connect(
        host=pg_host, port=pg_port, dbname=pg_db, user=pg_user, password=pg_password
    )
    if not target_database_exists(pg_conn, pg_db):
        pg_conn.close()
        driver.close()
        sys.exit(f"'{pg_db}' 데이터베이스가 {pg_host}:{pg_port}에 없습니다.")

    sync_run_id = generate_sync_run_id()
    print(f"syncRunId = {sync_run_id}")

    expected_default = get_default_database(driver)
    print(
        f"   현재 기본 데이터베이스: '{expected_default}' (승격 직전 재확인용으로 기록)"
    )

    try:
        print("1) PostgreSQL에서 노드·관계 전체 추출 (Neo4j는 아직 안 건드림)")
        extracted_nodes, extracted_rels = extract_all(pg_conn)

        print("2) 쓰기 전 검증 (0건 / 필수 키 NULL / 중복 키 / 참조 무결성)")
        pre_write_failures = validate_before_write(extracted_nodes, extracted_rels)
        if pre_write_failures:
            for failure in pre_write_failures:
                print(f"   - {failure}")
            sys.exit("쓰기 전 검증 실패 - Neo4j는 한 번도 안 건드렸습니다.")
        print("   쓰기 전 검증 전부 통과")

        print("3) 새 Neo4j 데이터베이스 생성")
        new_db = generate_database_name()
        create_neo4j_database(driver, new_db)
        print(f"   '{new_db}' 생성 완료 (기존 기본 데이터베이스는 그대로)")

        print("4) 제약조건 적용 + 노드·관계 적재 (새 데이터베이스 대상)")
        expected_node_counts, expected_rel_counts = load_all(
            driver, new_db, extracted_nodes, extracted_rels, sync_run_id
        )

        print("5) 적재 후 검증 (새 데이터베이스 대상)")
        post_load_failures = validate_after_load(
            driver, new_db, expected_node_counts, expected_rel_counts
        )
        if post_load_failures:
            print(f"   적재 후 검증 실패 {len(post_load_failures)}건:")
            for failure in post_load_failures:
                print(f"     - {failure}")
            sys.exit(
                f"검증 실패 - 새 데이터베이스 '{new_db}'는 조사를 위해 남겨뒀습니다. "
                "기존 기본 데이터베이스는 승격하지 않아 안전합니다."
            )

        print("6) 기본 데이터베이스로 승격")
        retry_promote(driver, new_db, expected_previous_default=expected_default)

        print("동기화 완료")
    finally:
        pg_conn.close()
        driver.close()


if __name__ == "__main__":
    main()
