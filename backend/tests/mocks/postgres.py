"""엔티티 정확 일치·유사도 조회 결과를 반환하는 PostgreSQL 테스트 mock."""

from typing import Any


class _MockCursor:
    def __init__(
        self,
        row: tuple[Any, ...] | None,
        rows: list[tuple[Any, ...]],
    ) -> None:
        self._row = row
        self._rows = rows

    def fetchone(self) -> tuple[Any, ...] | None:
        return self._row

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self._rows


class MockPostgresConnection:
    """이름별 정확 일치·유사도 조회 결과를 반환하고 마지막 execute 호출을 기록한다."""

    def __init__(
        self,
        rows_by_name: dict[str, tuple[Any, ...]],
        similar_rows_by_name: dict[str, list[tuple[Any, ...]]] | None = None,
    ) -> None:
        self._rows_by_name = rows_by_name
        self._similar_rows_by_name = similar_rows_by_name or {}
        self.last_query: tuple[str, tuple[Any, ...]] | None = None

    def execute(self, query: str, params: tuple[Any, ...] = ()) -> _MockCursor:
        self.last_query = (query, params)
        if not params:
            return _MockCursor(None, [])
        name = params[0]
        if "similarity(" in query:
            return _MockCursor(None, self._similar_rows_by_name.get(name, []))
        return _MockCursor(self._rows_by_name.get(name), [])
