"""GET /history 핸들러를 테스트한다."""

from datetime import datetime
from typing import Any

import api.history as history_module
from api.history import get_history
from core.auth import CurrentUser


class _FakeCursor:
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self._rows = rows

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self._rows


class _FakeConnection:
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self.last_query: tuple[str, tuple[Any, ...]] | None = None
        self._rows = rows

    def execute(self, query: str, params: tuple[Any, ...] = ()) -> _FakeCursor:
        self.last_query = (query, params)
        return _FakeCursor(self._rows)


def test_get_history_returns_own_rows_for_user(monkeypatch: Any) -> None:
    rows = [(1, "kim.quality", "q", "a", None, None, None, None, datetime(2026, 1, 1))]
    monkeypatch.setattr(history_module, "get_connection", lambda: _FakeConnection(rows))

    result = get_history(user=CurrentUser(username="kim.quality", role="user"))

    assert len(result) == 1
    assert result[0]["username"] == "kim.quality"


def test_get_history_scopes_query_by_role(monkeypatch: Any) -> None:
    connection = _FakeConnection([])
    monkeypatch.setattr(history_module, "get_connection", lambda: connection)

    get_history(user=CurrentUser(username="park.admin", role="admin"))

    assert connection.last_query is not None
    query, params = connection.last_query
    assert "WHERE" not in query
    assert params == ()
