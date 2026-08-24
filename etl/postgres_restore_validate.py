"""복원된 PostgreSQL에 유실 없이 데이터가 들어왔는지 사후 검증한다.

두 단계로 검증한다.
1. 테이블 단위: postgres_restore.parse_created_tables()가 plain SQL 덤프
   파일에서 뽑은 "CREATE TABLE로 만들어질 테이블 목록"과 실제 DB의 테이블
   목록을 비교한다(find_missing_tables).
2. 값 단위: queries/query_parameters.json의 entities에 있는 정확한
   정답값(제품명, 재고 수량, 작업지시 폐기수량 등)이 실제 DB 조회 결과와
   일치하는지 확인한다(build_fixture_checks/run_fixture_checks). 이 값들은
   원본 스냅샷에서 실제로 조회해 확정된 것이므로, 여기서 어긋나면 복원
   과정에서 데이터가 깨졌거나 유실됐다는 뜻이다.
"""

import json
import os
import sys
from pathlib import Path
from typing import Any, Protocol

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parent.parent

FixtureCheck = tuple[str, str, tuple[Any, ...], Any]  # (설명, SQL, 파라미터, 기대값)

_TABLES_SQL = (
    "SELECT table_schema || '.' || table_name FROM information_schema.tables "
    "WHERE table_type = 'BASE TABLE' AND table_schema NOT IN ('pg_catalog', 'information_schema')"
)


class _Cursor(Protocol):
    def execute(self, sql: str, params: tuple[Any, ...]) -> None: ...
    def fetchone(self) -> tuple[Any, ...]: ...
    def fetchall(self) -> list[tuple[Any, ...]]: ...


class _Connection(Protocol):
    def cursor(self) -> _Cursor: ...


def find_missing_tables(expected_tables: set[str], conn: _Connection) -> set[str]:
    """덤프 TOC에 있던 테이블 중 실제 DB에 없는 것을 찾는다."""
    with conn.cursor() as cursor:
        cursor.execute(_TABLES_SQL, ())
        actual_tables = {row[0] for row in cursor.fetchall()}
    return expected_tables - actual_tables


def build_fixture_checks(entities: dict[str, Any]) -> list[FixtureCheck]:
    """query_parameters.json의 entities에서 사후 검증용 SQL 체크 목록을 만든다."""
    checks: list[FixtureCheck] = []

    product_entities = [
        "pricedProduct",
        "multiLocationProduct",
        "riskComponent",
        "finishedProduct",
        "deepComponent",
        "comparisonProductA",
        "comparisonProductB",
    ]
    for key in product_entities:
        entity = entities[key]
        checks.append(
            (
                f"{key}: productId {entity['productId']} name",
                "SELECT name FROM production.product WHERE productid = %s",
                (entity["productId"],),
                entity["productName"],
            )
        )

    multi = entities["multiLocationProduct"]
    checks.append(
        (
            f"multiLocationProduct: productId {multi['productId']} 재고 행 수",
            "SELECT COUNT(*) FROM production.productinventory WHERE productid = %s",
            (multi["productId"],),
            multi["inventoryRowCount"],
        )
    )

    risk = entities["riskComponent"]
    checks.append(
        (
            f"riskComponent: productId {risk['productId']} 안전재고",
            "SELECT safetystocklevel FROM production.product WHERE productid = %s",
            (risk["productId"],),
            risk["safetyStockLevel"],
        )
    )
    checks.append(
        (
            f"riskComponent: productId {risk['productId']} 실재고 합계",
            "SELECT COALESCE(SUM(quantity), 0) FROM production.productinventory WHERE productid = %s",
            (risk["productId"],),
            risk["actualStock"],
        )
    )

    supplier = entities["supplier"]
    checks.append(
        (
            f"supplier: supplierId {supplier['supplierId']} 이름",
            "SELECT name FROM purchasing.vendor WHERE businessentityid = %s",
            (supplier["supplierId"],),
            supplier["supplierName"],
        )
    )
    checks.append(
        (
            f"supplier: suppliedProductId {supplier['suppliedProductId']} 이름",
            "SELECT name FROM production.product WHERE productid = %s",
            (supplier["suppliedProductId"],),
            supplier["suppliedProductName"],
        )
    )
    checks.append(
        (
            f"supplier: suppliedProductId {supplier['suppliedProductId']} 재고 합계",
            "SELECT COALESCE(SUM(quantity), 0) FROM production.productinventory WHERE productid = %s",
            (supplier["suppliedProductId"],),
            supplier["componentStock"],
        )
    )

    work_order = entities["workOrder"]
    checks.append(
        (
            f"workOrder: workOrderId {work_order['workOrderId']} productId·폐기수량·폐기사유",
            "SELECT productid, scrappedqty, scrapreasonid FROM production.workorder WHERE workorderid = %s",
            (work_order["workOrderId"],),
            (
                work_order["productId"],
                work_order["scrappedQty"],
                work_order["scrapReasonId"],
            ),
        )
    )
    checks.append(
        (
            f"workOrder: workOrderId {work_order['workOrderId']} 공정 수",
            "SELECT COUNT(*) FROM production.workorderrouting WHERE workorderid = %s",
            (work_order["workOrderId"],),
            work_order["operationCount"],
        )
    )

    category = entities["category"]
    checks.append(
        (
            f"category: categoryId {category['categoryId']} 이름",
            "SELECT name FROM production.productcategory WHERE productcategoryid = %s",
            (category["categoryId"],),
            category["categoryName"],
        )
    )
    checks.append(
        (
            f"category: categoryId {category['categoryId']} 제품 수",
            """
            SELECT COUNT(*) FROM production.productcategory pc
            JOIN production.productsubcategory ps ON ps.productcategoryid = pc.productcategoryid
            JOIN production.product p ON p.productsubcategoryid = ps.productsubcategoryid
            WHERE pc.productcategoryid = %s
            """,
            (category["categoryId"],),
            category["productCount"],
        )
    )

    return checks


def run_fixture_checks(conn: _Connection, checks: list[FixtureCheck]) -> list[str]:
    """체크 목록을 실행해 기대값과 다른 항목의 설명을 모은다(빈 리스트면 전부 통과)."""
    failures: list[str] = []
    with conn.cursor() as cursor:
        for description, sql, params, expected in checks:
            cursor.execute(sql, params)
            row = cursor.fetchone()
            if row is None:
                failures.append(f"{description}: 기대 {expected!r}, 실제 없음(행 유실)")
                continue
            actual = row[0] if len(row) == 1 else tuple(row)
            if actual != expected:
                failures.append(f"{description}: 기대 {expected!r}, 실제 {actual!r}")
    return failures


def main() -> None:
    """postgres_restore.py로 복원한 DB에 대해 테이블·픽스처 사후 검증을 실행한다.

    사용법(리포 루트 기준): python etl/postgres_restore_validate.py
    """
    load_dotenv(ROOT_DIR / ".env")

    import psycopg2
    from postgres_restore import REQUIRED_ENV_VARS, parse_created_tables

    missing_vars = [name for name in REQUIRED_ENV_VARS if not os.environ.get(name)]
    if missing_vars:
        sys.exit(f".env에 다음 값이 없습니다: {', '.join(missing_vars)}")

    host = os.environ["POSTGRES_HOST"]
    port = os.environ["POSTGRES_PORT"]
    db = os.environ["POSTGRES_DB"]
    user = os.environ["POSTGRES_USER"]
    password = os.environ.get("POSTGRES_PASSWORD", "")
    print(f"대상: {host}:{port}/{db}")

    dump_path = ROOT_DIR / "etl" / "data" / "AdventureWorksPG.sql"
    parameters_path = ROOT_DIR / "queries" / "query_parameters.json"

    print("1) 덤프 파일 재확인")
    if not dump_path.exists():
        sys.exit(f"덤프 파일이 없습니다: {dump_path}")
    expected_tables = parse_created_tables(dump_path)
    if not expected_tables:
        sys.exit(
            "덤프 파일에서 CREATE TABLE 문을 하나도 찾지 못했습니다 - "
            "파일이 손상됐거나 잘못된 파일일 수 있습니다."
        )
    print(f"   덤프에 정의된 테이블 {len(expected_tables)}개")

    conn = psycopg2.connect(
        host=host, port=port, dbname=db, user=user, password=password
    )

    try:
        print("2) 테이블 존재 여부 대조")
        missing_tables = find_missing_tables(expected_tables, conn)
        if missing_tables:
            print(f"   누락된 테이블 {len(missing_tables)}개: {sorted(missing_tables)}")
        else:
            print(f"   전체 {len(expected_tables)}개 테이블 확인됨, 누락 없음")

        print("3) 픽스처 값 대조 (query_parameters.json 기준)")
        entities = json.loads(parameters_path.read_text(encoding="utf-8"))["entities"]
        checks = build_fixture_checks(entities)
        failures = run_fixture_checks(conn, checks)
        if failures:
            print(f"   불일치 {len(failures)}건:")
            for failure in failures:
                print(f"     - {failure}")
        else:
            print(f"   {len(checks)}개 항목 전부 일치")

        if missing_tables or failures:
            sys.exit(1)
        print("사후 검증 통과: 유실/손상된 데이터 없음")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
