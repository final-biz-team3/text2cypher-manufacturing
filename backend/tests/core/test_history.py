"""대화기록 저장·조회 동작을 테스트한다."""

from datetime import datetime
from typing import Any

from core.auth import CurrentUser
from core.history import delete_conversation, list_history, save_conversation


class _FakeCursor:
    def __init__(self, rows: list[tuple[Any, ...]], rowcount: int = 0) -> None:
        self._rows = rows
        self.rowcount = rowcount

    async def fetchall(self) -> list[tuple[Any, ...]]:
        return self._rows


class _FakeConnection:
    def __init__(self, pool: "_FakePool") -> None:
        self._pool = pool

    async def execute(self, query: str, params: tuple[Any, ...] = ()) -> _FakeCursor:
        self._pool.statements.append((query, params))
        if query.startswith("DELETE"):
            return _FakeCursor([], rowcount=self._pool.delete_rowcount)
        return _FakeCursor(self._pool.rows)

    async def commit(self) -> None:
        self._pool.committed = True


class _FakeConnectionContext:
    def __init__(self, pool: "_FakePool") -> None:
        self._pool = pool

    async def __aenter__(self) -> _FakeConnection:
        return _FakeConnection(self._pool)

    async def __aexit__(self, *exc_info: object) -> bool:
        return False


class _FakePool:
    def __init__(
        self,
        rows: list[tuple[Any, ...]] | None = None,
        delete_rowcount: int = 0,
    ) -> None:
        self.statements: list[tuple[str, tuple[Any, ...]]] = []
        self.committed = False
        self.rows = rows or []
        self.delete_rowcount = delete_rowcount

    def connection(self, timeout: float | None = None) -> _FakeConnectionContext:
        return _FakeConnectionContext(self)


async def test_save_conversation_inserts_and_commits() -> None:
    pool = _FakePool()

    await save_conversation(
        pool,
        "kim.quality",
        "정가 알려줘",
        "1200원입니다",
        "SELECT listprice FROM production.product",
        None,
        {
            "result": [{"listprice": 1200}],
            "error": None,
            "attempts": [],
            "empty_reason": None,
        },
        None,
    )

    query, params = pool.statements[0]
    assert "INSERT INTO app.conversation_history" in query
    assert params[0] == "kim.quality"
    assert params[1] == "정가 알려줘"
    assert params[2] == "1200원입니다"
    assert '"listprice": 1200' in params[5]
    assert params[6] is None
    assert pool.committed is True


async def test_list_history_scopes_to_own_rows_for_non_admin() -> None:
    pool = _FakePool(
        rows=[
            (1, "kim.quality", "q", "a", None, None, None, None, datetime(2026, 1, 1))
        ]
    )

    result = await list_history(pool, CurrentUser(username="kim.quality", role="user"))

    query, params = pool.statements[0]
    assert "WHERE username = %s" in query
    assert params == ("kim.quality",)
    assert result == [
        {
            "id": 1,
            "username": "kim.quality",
            "query": "q",
            "final_answer": "a",
            "sql_query": None,
            "cypher_query": None,
            "sql_result": None,
            "graph_result": None,
            "created_at": "2026-01-01T00:00:00",
        }
    ]


async def test_list_history_returns_all_rows_for_admin() -> None:
    pool = _FakePool(rows=[])

    await list_history(pool, CurrentUser(username="park.admin", role="admin"))

    query, params = pool.statements[0]
    assert "WHERE" not in query
    assert params == ()


async def test_delete_conversation_deletes_own_row_for_non_admin() -> None:
    pool = _FakePool(delete_rowcount=1)

    deleted = await delete_conversation(
        pool, CurrentUser(username="kim.quality", role="user"), 42
    )

    query, params = pool.statements[0]
    assert "DELETE FROM app.conversation_history" in query
    assert "WHERE id = %s AND username = %s" in query
    assert params == (42, "kim.quality")
    assert pool.committed is True
    assert deleted is True


async def test_delete_conversation_returns_false_when_not_found() -> None:
    pool = _FakePool(delete_rowcount=0)

    deleted = await delete_conversation(
        pool, CurrentUser(username="kim.quality", role="user"), 42
    )

    assert deleted is False


async def test_delete_conversation_admin_deletes_any_row_without_username_filter() -> (
    None
):
    pool = _FakePool(delete_rowcount=1)

    deleted = await delete_conversation(
        pool, CurrentUser(username="park.admin", role="admin"), 7
    )

    query, params = pool.statements[0]
    assert "WHERE id = %s" in query
    assert "username" not in query
    assert params == (7,)
    assert deleted is True
