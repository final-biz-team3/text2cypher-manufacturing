"""SQL 쿼리 가드가 쓰기 절과 미허가 테이블을 차단하는지 검증한다."""

from agents.sql.schema.models import SqlSchema
from orchestrator.guards.sql_guard import make_sql_guard

_SCHEMA = SqlSchema.model_validate(
    {
        "tables": {
            "production.product": {
                "columns": {
                    "productid": {"type": "INTEGER"},
                    "name": {"type": "VARCHAR"},
                }
            }
        },
        "joins": [],
    }
)


def test_sql_guard_allows_plain_select() -> None:
    guard = make_sql_guard(_SCHEMA)

    result = guard("SELECT productid, name FROM production.product WHERE productid = 1")

    assert result.allowed is True


def test_sql_guard_allows_read_only_with_cte() -> None:
    guard = make_sql_guard(_SCHEMA)

    result = guard(
        "WITH p AS (SELECT productid FROM production.product) "
        "SELECT productid FROM p"
    )

    assert result.allowed is True


def test_sql_guard_blocks_insert() -> None:
    guard = make_sql_guard(_SCHEMA)

    result = guard("INSERT INTO production.product (productid) VALUES (1)")

    assert result.allowed is False
    assert result.reason_code == "WRITE_KEYWORD_DETECTED"


def test_sql_guard_blocks_write_smuggled_in_cte() -> None:
    """WITH 절 안에 DELETE가 숨어 있어도 최상위가 SELECT면 통과시키지 않는다."""
    guard = make_sql_guard(_SCHEMA)

    result = guard(
        "WITH deleted AS (DELETE FROM production.product RETURNING productid) "
        "SELECT productid FROM deleted"
    )

    assert result.allowed is False
    assert result.reason_code == "WRITE_KEYWORD_DETECTED"


def test_sql_guard_blocks_multiple_statements() -> None:
    guard = make_sql_guard(_SCHEMA)

    result = guard("SELECT 1; DROP TABLE production.product;")

    assert result.allowed is False


def test_sql_guard_blocks_unknown_table() -> None:
    guard = make_sql_guard(_SCHEMA)

    result = guard("SELECT * FROM pg_catalog.pg_shadow")

    assert result.allowed is False
    assert result.reason_code == "UNKNOWN_TABLE"
