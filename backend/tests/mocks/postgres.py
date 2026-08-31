"""엔티티 정확 일치·유사도 조회 결과를 반환하는 PostgreSQL 풀 테스트 mock."""

from typing import Any


class _MockAsyncCursor:
    def __init__(
        self,
        row: tuple[Any, ...] | None,
        rows: list[tuple[Any, ...]],
    ) -> None:
        self._row = row
        self._rows = rows

    async def fetchone(self) -> tuple[Any, ...] | None:
        return self._row

    async def fetchall(self) -> list[tuple[Any, ...]]:
        return self._rows


class _MockAsyncConnection:
    def __init__(self, pool: "MockAsyncPostgresPool") -> None:
        self._pool = pool

    async def execute(
        self, query: str, params: tuple[Any, ...] = ()
    ) -> _MockAsyncCursor:
        return self._pool._execute(query, params)

    async def rollback(self) -> None:
        self._pool.rollback_called = True


class _ConnectionContext:
    def __init__(self, pool: "MockAsyncPostgresPool") -> None:
        self._pool = pool

    async def __aenter__(self) -> _MockAsyncConnection:
        return _MockAsyncConnection(self._pool)

    async def __aexit__(self, *exc_info: object) -> bool:
        return False


class MockAsyncPostgresPool:
    """이름별 정확 일치·유사도 조회 결과를 반환하고 마지막 execute 호출을 기록한다.
    psycopg_pool.AsyncConnectionPool의 `async with pool.connection() as conn`
    사용 패턴을 그대로 흉내낸다."""

    def __init__(
        self,
        rows_by_name: dict[str, tuple[Any, ...]],
        similar_rows_by_name: dict[str, list[tuple[Any, ...]]] | None = None,
        similarity_error: Exception | None = None,
        rows_by_table_and_name: dict[tuple[str, str], tuple[Any, ...]] | None = None,
        contained_rows_by_table_and_query: (
            dict[tuple[str, str], list[tuple[Any, ...]]] | None
        ) = None,
    ) -> None:
        self._rows_by_name = rows_by_name
        self._rows_by_table_and_name = rows_by_table_and_name or {}
        self._contained_rows_by_table_and_query = (
            contained_rows_by_table_and_query or {}
        )
        self._similar_rows_by_name = similar_rows_by_name or {}
        self._similarity_error = similarity_error
        self.last_query: tuple[str, tuple[Any, ...]] | None = None
        self.rollback_called = False

    def connection(self) -> _ConnectionContext:
        return _ConnectionContext(self)

    def _execute(self, query: str, params: tuple[Any, ...]) -> _MockAsyncCursor:
        self.last_query = (query, params)
        if not params:
            return _MockAsyncCursor(None, [])
        if "similarity(" in query:
            if self._similarity_error is not None:
                raise self._similarity_error
            name = params[0]
            return _MockAsyncCursor(None, self._similar_rows_by_name.get(name, []))
        if "strpos(lower(" in query:
            source_query = params[0]
            for (
                table,
                candidate_query,
            ), rows in self._contained_rows_by_table_and_query.items():
                if candidate_query == source_query and f"FROM {table}" in query:
                    return _MockAsyncCursor(None, rows)
            return _MockAsyncCursor(None, [])
        if len(params) == 2:
            exists = params in self._rows_by_name.values()
            return _MockAsyncCursor((1,) if exists else None, [])
        name = params[0]
        for (table, candidate_name), row in self._rows_by_table_and_name.items():
            if candidate_name == name and f"FROM {table}" in query:
                return _MockAsyncCursor(row, [])
        return _MockAsyncCursor(self._rows_by_name.get(name), [])


class _MockAsyncWriteConnection:
    def __init__(self, pool: "MockAsyncWritePool") -> None:
        self._pool = pool

    async def execute(
        self, query: str, params: tuple[Any, ...] = ()
    ) -> "_MockAsyncWriteCursor":
        self._pool.statements.append((query, params))
        return _MockAsyncWriteCursor(self._pool.rows)

    async def commit(self) -> None:
        self._pool.committed = True

    async def rollback(self) -> None:
        self._pool.rollback_called = True


class _MockAsyncWriteCursor:
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self._rows = rows

    async def fetchall(self) -> list[tuple[Any, ...]]:
        return self._rows


class _WriteConnectionContext:
    def __init__(self, pool: "MockAsyncWritePool") -> None:
        self._pool = pool

    async def __aenter__(self) -> _MockAsyncWriteConnection:
        return _MockAsyncWriteConnection(self._pool)

    async def __aexit__(self, *exc_info: object) -> bool:
        return False


class MockAsyncWritePool:
    """save_conversation/list_history 같은 앱 코드 직접 쓰기·조회 쿼리를 그대로
    기록만 하는 write pool mock. 엔티티 조회용 MockAsyncPostgresPool과 달리
    쿼리 문형을 추측해 분기하지 않고, 실행된 statement를 순서대로 쌓아둔다."""

    def __init__(self, rows: list[tuple[Any, ...]] | None = None) -> None:
        self.rows = rows or []
        self.statements: list[tuple[str, tuple[Any, ...]]] = []
        self.committed = False
        self.rollback_called = False

    def connection(self) -> _WriteConnectionContext:
        return _WriteConnectionContext(self)

    def get_stats(self) -> dict[str, Any]:
        return {}
