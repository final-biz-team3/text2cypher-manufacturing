"""GET /history 핸들러를 테스트한다."""

from datetime import datetime
from typing import Any

import pytest
from fastapi import HTTPException

import api.history as history_module
from api.history import delete_history, get_history
from core.auth import CurrentUser
from tests.mocks.postgres import MockAsyncWritePool


class _FakeCursor:
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self._rows = rows

    async def fetchall(self) -> list[tuple[Any, ...]]:
        return self._rows


class _FakeConnection:
    def __init__(self, pool: "_FakePool") -> None:
        self._pool = pool

    async def execute(self, query: str, params: tuple[Any, ...] = ()) -> _FakeCursor:
        self._pool.last_query = (query, params)
        return _FakeCursor(self._pool.rows)


class _FakeConnectionContext:
    def __init__(self, pool: "_FakePool") -> None:
        self._pool = pool

    async def __aenter__(self) -> _FakeConnection:
        return _FakeConnection(self._pool)

    async def __aexit__(self, *exc_info: object) -> bool:
        return False


class _FakePool:
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self.last_query: tuple[str, tuple[Any, ...]] | None = None
        self.rows = rows

    def connection(self) -> _FakeConnectionContext:
        return _FakeConnectionContext(self)


async def test_get_history_returns_own_rows_for_user(monkeypatch: Any) -> None:
    rows = [
        (1, "kim.quality", "q", "a", None, None, None, None, None, datetime(2026, 1, 1))
    ]
    monkeypatch.setattr(history_module, "get_pool", lambda: _FakePool(rows))

    result = await get_history(user=CurrentUser(username="kim.quality", role="user"))

    assert len(result) == 1
    assert result[0]["username"] == "kim.quality"


async def test_get_history_scopes_query_by_role(monkeypatch: Any) -> None:
    pool = _FakePool([])
    monkeypatch.setattr(history_module, "get_pool", lambda: pool)

    await get_history(user=CurrentUser(username="park.admin", role="admin"))

    assert pool.last_query is not None
    query, params = pool.last_query
    assert "WHERE" not in query
    assert params == ()


async def test_delete_history_deletes_own_row_and_returns_no_content(
    monkeypatch: Any,
) -> None:
    pool = MockAsyncWritePool(rowcount=1)
    monkeypatch.setattr(history_module, "get_write_pool", lambda: pool)

    await delete_history(
        history_id=42, user=CurrentUser(username="kim.quality", role="user")
    )

    assert pool.statements
    query, params = pool.statements[0]
    assert "DELETE FROM app.conversation_history" in query
    assert params == (42, "kim.quality")
    assert pool.committed is True


async def test_delete_history_raises_404_when_not_found(monkeypatch: Any) -> None:
    pool = MockAsyncWritePool(rowcount=0)
    monkeypatch.setattr(history_module, "get_write_pool", lambda: pool)

    with pytest.raises(HTTPException) as excinfo:
        await delete_history(
            history_id=42, user=CurrentUser(username="kim.quality", role="user")
        )

    assert excinfo.value.status_code == 404
