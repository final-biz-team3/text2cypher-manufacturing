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
    ) -> None:
        self._rows_by_name = rows_by_name
        self._rows_by_table_and_name = rows_by_table_and_name or {}
        self._similar_rows_by_name = similar_rows_by_name or {}
        self._similarity_error = similarity_error
        self.last_query: tuple[str, tuple[Any, ...]] | None = None
        self.queries: list[tuple[str, tuple[Any, ...]]] = []
        self.rollback_called = False

    def connection(self) -> _ConnectionContext:
        return _ConnectionContext(self)

    def _execute(self, query: str, params: tuple[Any, ...]) -> _MockAsyncCursor:
        self.last_query = (query, params)
        self.queries.append((query, params))
        if not params:
            return _MockAsyncCursor(None, [])
        if "similarity(" in query:
            if self._similarity_error is not None:
                raise self._similarity_error
            name = params[0]
            rows = self._similar_rows_by_name.get(name, [])
            # 실제 SQL의 LIMIT %s(마지막 파라미터)를 그대로 흉내낸다 - 안
            # 그러면 LIMIT을 넘겨받고도 mock이 목록을 안 자르는 통에 "top-N
            # 밖으로 밀려난 후보" 관련 회귀를 테스트로 못 잡는다.
            limit = params[-1]
            if isinstance(limit, int) and not isinstance(limit, bool):
                rows = rows[:limit]
            return _MockAsyncCursor(None, rows)
        if "= ANY(%s)" in query:
            names = params[0]
            rows = []
            if isinstance(names, list):
                for name in names:
                    matched_specific = False
                    for (
                        table,
                        candidate_name,
                    ), row in self._rows_by_table_and_name.items():
                        if candidate_name == name and f"FROM {table} " in query:
                            rows.append(row)
                            matched_specific = True
                    if not matched_specific and name in self._rows_by_name:
                        rows.append(self._rows_by_name[name])
            return _MockAsyncCursor(None, rows)
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
        return _MockAsyncWriteCursor(self._pool.rows, self._pool.rowcount)

    async def commit(self) -> None:
        self._pool.committed = True

    async def rollback(self) -> None:
        self._pool.rollback_called = True


class _MockAsyncWriteCursor:
    def __init__(self, rows: list[tuple[Any, ...]], rowcount: int = 0) -> None:
        self._rows = rows
        self.rowcount = rowcount

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

    def __init__(
        self, rows: list[tuple[Any, ...]] | None = None, rowcount: int = 0
    ) -> None:
        self.rows = rows or []
        self.rowcount = rowcount
        self.statements: list[tuple[str, tuple[Any, ...]]] = []
        self.committed = False
        self.rollback_called = False

    def connection(self) -> _WriteConnectionContext:
        return _WriteConnectionContext(self)

    def get_stats(self) -> dict[str, Any]:
        return {}
