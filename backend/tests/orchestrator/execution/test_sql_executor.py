"""execute_sql이 dict_row 결과·행 상한·rollback 순서를 지키는지 검증한다."""

import psycopg
import pytest

from orchestrator.execution.sql_executor import execute_sql_with_pool


async def test_execute_sql_returns_dict_rows_and_rolls_back() -> None:
    """실행 결과를 dict 리스트로 반환하고 fetch 이후 rollback을 호출한다."""

    class _MockDictCursor:
        def __init__(self, conn: "_MockDictConn") -> None:
            self._conn = conn

        async def __aenter__(self) -> "_MockDictCursor":
            return self

        async def __aexit__(self, *exc_info: object) -> bool:
            return False

        async def execute(self, sql: str, params: tuple = ()) -> None:
            return None

        async def fetchmany(self, n: int) -> list[dict]:
            return [{"productid": 492, "name": "Paint - Black"}][:n]

    class _MockDictConn:
        def __init__(self) -> None:
            self.rollback_called = False

        def cursor(self, row_factory=None) -> _MockDictCursor:
            return _MockDictCursor(self)

        async def rollback(self) -> None:
            self.rollback_called = True

    class _ConnContext:
        def __init__(self, conn: _MockDictConn) -> None:
            self._conn = conn

        async def __aenter__(self) -> _MockDictConn:
            return self._conn

        async def __aexit__(self, *exc_info: object) -> bool:
            return False

    class _MockPool:
        def __init__(self) -> None:
            self.conn = _MockDictConn()

        def connection(self) -> _ConnContext:
            return _ConnContext(self.conn)

    pool = _MockPool()

    rows = await execute_sql_with_pool(
        pool, "SELECT productid, name FROM production.product", row_limit=10
    )

    assert rows == [{"productid": 492, "name": "Paint - Black"}]
    assert pool.conn.rollback_called is True


async def test_execute_sql_truncates_to_row_limit() -> None:
    """fetchmany가 row_limit보다 많이 돌려줘도 row_limit개로 자른다."""

    class _MockDictCursor:
        async def __aenter__(self) -> "_MockDictCursor":
            return self

        async def __aexit__(self, *exc_info: object) -> bool:
            return False

        async def execute(self, sql: str, params: tuple = ()) -> None:
            return None

        async def fetchmany(self, n: int) -> list[dict]:
            return [{"id": i} for i in range(n)]

    class _MockDictConn:
        def cursor(self, row_factory=None) -> _MockDictCursor:
            return _MockDictCursor()

        async def rollback(self) -> None:
            pass

    class _ConnContext:
        async def __aenter__(self) -> _MockDictConn:
            return _MockDictConn()

        async def __aexit__(self, *exc_info: object) -> bool:
            return False

    class _MockPool:
        def connection(self) -> _ConnContext:
            return _ConnContext()

    rows = await execute_sql_with_pool(_MockPool(), "SELECT 1", row_limit=3)

    assert len(rows) == 3


async def test_execute_sql_propagates_original_exception_without_wrapping() -> None:
    """실행 중 예외가 나면 원본 타입 그대로 전파하고, 전파 전 rollback을 호출한다."""

    class _MockDictCursor:
        def __init__(self, conn: "_MockDictConn") -> None:
            self._conn = conn

        async def __aenter__(self) -> "_MockDictCursor":
            return self

        async def __aexit__(self, *exc_info: object) -> bool:
            return False

        async def execute(self, sql: str, params: tuple = ()) -> None:
            raise psycopg.errors.UndefinedColumn("column bad_column does not exist")

        async def fetchmany(self, n: int) -> list[dict]:
            return []

    class _MockDictConn:
        def __init__(self) -> None:
            self.rollback_called = False

        def cursor(self, row_factory=None) -> _MockDictCursor:
            return _MockDictCursor(self)

        async def rollback(self) -> None:
            self.rollback_called = True

    class _ConnContext:
        def __init__(self, conn: _MockDictConn) -> None:
            self._conn = conn

        async def __aenter__(self) -> _MockDictConn:
            return self._conn

        async def __aexit__(self, *exc_info: object) -> bool:
            return False

    class _MockPool:
        def __init__(self) -> None:
            self.conn = _MockDictConn()

        def connection(self) -> _ConnContext:
            return _ConnContext(self.conn)

    pool = _MockPool()

    with pytest.raises(psycopg.errors.UndefinedColumn):
        await execute_sql_with_pool(pool, "SELECT bad_column", row_limit=10)

    assert pool.conn.rollback_called is True
