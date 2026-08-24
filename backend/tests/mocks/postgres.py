"""production.product 정확 일치 조회를 제공하는 PostgreSQL 테스트 mock."""

from typing import Any


class _MockCursor:
    def __init__(self, row: tuple[Any, ...] | None) -> None:
        self._row = row

    def fetchone(self) -> tuple[Any, ...] | None:
        return self._row


class MockPostgresConnection:
    """제품명별 조회 결과를 반환하고 마지막 execute 호출을 기록한다."""

    def __init__(self, rows_by_name: dict[str, tuple[Any, ...]]) -> None:
        self._rows_by_name = rows_by_name
        self.last_query: tuple[str, tuple[Any, ...]] | None = None

    def execute(self, query: str, params: tuple[Any, ...] = ()) -> _MockCursor:
        self.last_query = (query, params)
        if not params:
            return _MockCursor(None)
        name = params[0]
        return _MockCursor(self._rows_by_name.get(name))
