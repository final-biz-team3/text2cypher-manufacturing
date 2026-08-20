"""복원 후 사후 검증(테이블 존재 비교, 픽스처 값 대조) 로직을 검증한다.

find_missing_tables/run_fixture_checks는 DB 커서를 쓰지만, 이 테스트에서는
진짜 psycopg2 연결 대신 손으로 만든 가짜 커서(FakeCursor)를 넘겨 순수하게
비교 로직만 검증한다. 진짜 DB로 하는 검증은 로컬 docker 환경에서 진행한다.
"""

from postgres_restore_validate import (
    build_fixture_checks,
    find_missing_tables,
    run_fixture_checks,
)

SAMPLE_ENTITIES = {
    "pricedProduct": {"productId": 956, "productName": "Touring-1000 Yellow, 54"},
    "multiLocationProduct": {
        "productId": 747,
        "productName": "HL Mountain Frame - Black, 38",
        "inventoryRowCount": 6,
    },
    "riskComponent": {
        "productId": 492,
        "productName": "Paint - Black",
        "safetyStockLevel": 60,
        "actualStock": 47,
        "shortageQty": 13,
    },
    "finishedProduct": {"productId": 680, "productName": "HL Road Frame - Black, 58"},
    "deepComponent": {"productId": 486, "productName": "Metal Sheet 5"},
    "comparisonProductA": {"productId": 765, "productName": "Road-650 Black, 58"},
    "comparisonProductB": {"productId": 775, "productName": "Mountain-100 Black, 38"},
    "supplier": {
        "supplierId": 1494,
        "supplierName": "Allenson Cycles",
        "suppliedProductId": 530,
        "suppliedProductName": "Seat Post",
        "componentStock": 780,
    },
    "workOrder": {
        "workOrderId": 17747,
        "productId": 802,
        "productName": "LL Fork",
        "scrappedQty": 54,
        "scrapReasonId": 8,
        "operationCount": 2,
    },
    "category": {"categoryId": 2, "categoryName": "Components", "productCount": 134},
}


class FakeCursor:
    """(sql, params) -> row 매핑으로 동작하는 psycopg2 커서 스텁."""

    def __init__(self, responses: dict[tuple[str, tuple], tuple]) -> None:
        self._responses = responses
        self._last_row: tuple | None = None

    def __enter__(self) -> "FakeCursor":
        return self

    def __exit__(self, *exc_info: object) -> None:
        return None

    def execute(self, sql: str, params: tuple) -> None:
        key = (" ".join(sql.split()), params)
        self._last_row = self._responses[key]

    def fetchone(self) -> tuple | None:
        return self._last_row

    def fetchall(self) -> list[tuple]:
        raise NotImplementedError


class FakeConnection:
    def __init__(self, cursor: FakeCursor) -> None:
        self._cursor = cursor

    def cursor(self) -> FakeCursor:
        return self._cursor


def test_build_fixture_checks_covers_every_entity() -> None:
    checks = build_fixture_checks(SAMPLE_ENTITIES)

    descriptions = [description for description, *_ in checks]
    assert len(checks) == 17
    assert any("pricedProduct" in d for d in descriptions)
    assert any("multiLocationProduct" in d and "재고 행 수" in d for d in descriptions)
    assert any("category" in d and "제품 수" in d for d in descriptions)


def test_run_fixture_checks_returns_empty_list_when_all_match() -> None:
    checks = build_fixture_checks(SAMPLE_ENTITIES)
    responses = {
        (" ".join(sql.split()), params): (
            (expected,) if not isinstance(expected, tuple) else expected
        )
        for _, sql, params, expected in checks
    }
    conn = FakeConnection(FakeCursor(responses))

    failures = run_fixture_checks(conn, checks)

    assert failures == []


def test_run_fixture_checks_reports_mismatch() -> None:
    checks = build_fixture_checks(SAMPLE_ENTITIES)
    responses = {
        (" ".join(sql.split()), params): (
            (expected,) if not isinstance(expected, tuple) else expected
        )
        for _, sql, params, expected in checks
    }
    # pricedProduct 이름을 원본과 다르게 조작해 복원 유실/손상 상황을 흉내낸다
    price_check = next(c for c in checks if "pricedProduct" in c[0])
    key = (" ".join(price_check[1].split()), price_check[2])
    responses[key] = ("Wrong Name",)
    conn = FakeConnection(FakeCursor(responses))

    failures = run_fixture_checks(conn, checks)

    assert len(failures) == 1
    assert "pricedProduct" in failures[0]
    assert "Wrong Name" in failures[0]


def test_run_fixture_checks_reports_missing_row_instead_of_crashing() -> None:
    checks = build_fixture_checks(SAMPLE_ENTITIES)
    responses = {
        (" ".join(sql.split()), params): (
            (expected,) if not isinstance(expected, tuple) else expected
        )
        for _, sql, params, expected in checks
    }
    # pricedProduct 행 자체가 유실된 상황을 흉내낸다(fetchone()이 None 반환)
    price_check = next(c for c in checks if "pricedProduct" in c[0])
    key = (" ".join(price_check[1].split()), price_check[2])
    responses[key] = None
    conn = FakeConnection(FakeCursor(responses))

    failures = run_fixture_checks(conn, checks)

    assert len(failures) == 1
    assert "pricedProduct" in failures[0]
    assert "유실" in failures[0]


def test_find_missing_tables_returns_tables_absent_from_actual() -> None:
    expected = {"production.product", "purchasing.vendor", "production.workorder"}
    cursor = FakeCursor(
        {
            (
                "SELECT table_schema || '.' || table_name FROM information_schema.tables "
                "WHERE table_type = 'BASE TABLE' AND table_schema NOT IN ('pg_catalog', 'information_schema')",
                (),
            ): None
        }
    )

    def fetchall() -> list[tuple]:
        return [("production.product",), ("purchasing.vendor",)]

    cursor.fetchall = fetchall  # type: ignore[method-assign]
    conn = FakeConnection(cursor)

    missing = find_missing_tables(expected, conn)

    assert missing == {"production.workorder"}
