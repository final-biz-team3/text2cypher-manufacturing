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


def test_sql_guard_blocks_do_block() -> None:
    """DO $$ ... $$는 익명 PL/pgSQL 블록으로 그 안에서 사실상 임의 코드를
    실행할 수 있다 - $$...$$ 안에 세미콜론이 있어도 sqlparse가 단일
    statement로 인식하므로(실측 확인) MULTIPLE_STATEMENTS가 아니라
    WRITE_KEYWORD_DETECTED로 잡혀야 한다."""
    guard = make_sql_guard(_SCHEMA)

    result = guard(
        "DO $$ BEGIN " "UPDATE production.product SET name = 'x'; " "END $$;"
    )

    assert result.allowed is False
    assert result.reason_code == "WRITE_KEYWORD_DETECTED"


def test_sql_guard_blocks_lock_table() -> None:
    guard = make_sql_guard(_SCHEMA)

    result = guard("LOCK TABLE production.product")

    assert result.allowed is False
    assert result.reason_code == "WRITE_KEYWORD_DETECTED"


def test_sql_guard_blocks_unqualified_table_reference() -> None:
    """스키마 없이 쓰인 테이블 참조(FROM pg_shadow)는 정규식 추출망을 빠져나가
    화이트리스트 검사 자체를 통과하던 우회였다 - 코드 리뷰로 발견됨. 이제는
    해석 불가 참조로 보고 fail-closed 한다."""
    guard = make_sql_guard(_SCHEMA)

    result = guard("SELECT * FROM pg_shadow")

    assert result.allowed is False
    assert result.reason_code == "UNRESOLVED_TABLE_REFERENCE"


def test_sql_guard_blocks_second_table_in_comma_separated_list() -> None:
    """콤마로 나열된 두 번째 이후 테이블은 예전 정규식이 못 봤다 - 코드
    리뷰로 발견된 우회. pg_catalog는 PostgreSQL이 암묵적으로 검색하는
    스키마라 시스템 카탈로그 노출로 이어질 수 있었다."""
    guard = make_sql_guard(_SCHEMA)

    result = guard("SELECT * FROM production.product p, pg_catalog.pg_shadow s")

    assert result.allowed is False
    assert result.reason_code == "UNKNOWN_TABLE"
    assert "pg_catalog.pg_shadow" in (result.reason_detail or "")


def test_sql_guard_blocks_unknown_table_hidden_in_subquery() -> None:
    guard = make_sql_guard(_SCHEMA)

    result = guard("SELECT * FROM (SELECT * FROM pg_catalog.pg_shadow) sub")

    assert result.allowed is False
    assert result.reason_code == "UNKNOWN_TABLE"


def test_sql_guard_allows_known_table_inside_subquery() -> None:
    guard = make_sql_guard(_SCHEMA)

    result = guard("SELECT * FROM (SELECT productid FROM production.product) sub")

    assert result.allowed is True


def test_sql_guard_allows_multiple_cte_definitions() -> None:
    """CTE 이름이 여러 개 정의돼도(콤마 나열) 실제 테이블 검사에서 제외돼야 한다."""
    guard = make_sql_guard(_SCHEMA)

    result = guard(
        "WITH a AS (SELECT productid FROM production.product), "
        "b AS (SELECT productid FROM production.product) "
        "SELECT * FROM a JOIN b ON a.productid = b.productid"
    )

    assert result.allowed is True


def test_sql_guard_blocks_pg_advisory_lock_function_call() -> None:
    """세션 레벨 advisory lock은 SELECT 형태를 유지한 채 키워드/테이블 검사를
    모두 우회한다 - 코드 리뷰로 발견됨. rollback으로도 안 풀리는 잠금이라
    반복 호출 시 잠금/공유 메모리 고갈로 이어질 수 있다."""
    guard = make_sql_guard(_SCHEMA)

    result = guard("SELECT pg_advisory_lock(42)")

    assert result.allowed is False
    assert result.reason_code == "FORBIDDEN_FUNCTION_CALL"


def test_sql_guard_blocks_pg_advisory_lock_inside_from_clause() -> None:
    guard = make_sql_guard(_SCHEMA)

    result = guard("SELECT pg_advisory_lock(i) FROM generate_series(1,5) i")

    assert result.allowed is False
    assert result.reason_code == "FORBIDDEN_FUNCTION_CALL"


def test_sql_guard_blocks_select_into() -> None:
    """SELECT ... INTO는 sqlparse상 SELECT 타입이지만 새 테이블을 만드는
    쓰기 작업이다 - 코드 리뷰로 발견됨."""
    guard = make_sql_guard(_SCHEMA)

    result = guard("SELECT * INTO temp_table FROM production.product")

    assert result.allowed is False
    assert result.reason_code == "WRITE_KEYWORD_DETECTED"


def test_sql_guard_blocks_unknown_table_in_select_list_subquery() -> None:
    """FROM/JOIN 뒤가 아닌 SELECT절 스칼라 서브쿼리는 테이블 순회에서
    빠지던 우회였다 - 자체 리뷰로 발견 및 재현됨."""
    guard = make_sql_guard(_SCHEMA)

    result = guard(
        "SELECT (SELECT password FROM pg_shadow LIMIT 1) FROM production.product"
    )

    assert result.allowed is False


def test_sql_guard_blocks_unknown_table_in_where_in_subquery() -> None:
    guard = make_sql_guard(_SCHEMA)

    result = guard(
        "SELECT productid FROM production.product "
        "WHERE productid IN (SELECT id FROM pg_shadow)"
    )

    assert result.allowed is False


def test_sql_guard_blocks_unknown_table_in_where_exists_subquery() -> None:
    guard = make_sql_guard(_SCHEMA)

    result = guard(
        "SELECT * FROM production.product "
        "WHERE EXISTS (SELECT 1 FROM pg_catalog.pg_shadow)"
    )

    assert result.allowed is False
    assert result.reason_code == "UNKNOWN_TABLE"


def test_sql_guard_blocks_unknown_table_in_any_subquery() -> None:
    guard = make_sql_guard(_SCHEMA)

    result = guard(
        "SELECT * FROM production.product "
        "WHERE productid = ANY(SELECT id FROM pg_shadow)"
    )

    assert result.allowed is False


def test_sql_guard_allows_with_recursive() -> None:
    """WITH 다음의 RECURSIVE 키워드를 CTE 이름으로 오인해 정상 재귀 CTE
    쿼리를 오탐 차단하던 회귀 - 자체 리뷰로 발견 및 재현됨."""
    guard = make_sql_guard(_SCHEMA)

    result = guard(
        "WITH RECURSIVE x AS (SELECT productid FROM production.product) "
        "SELECT * FROM x"
    )

    assert result.allowed is True
