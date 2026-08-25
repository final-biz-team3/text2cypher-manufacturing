"""대화기록 저장·조회 동작을 테스트한다."""

from datetime import datetime
from typing import Any

from core.auth import CurrentUser
from core.history import list_history, save_conversation


class _FakeCursor:
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self._rows = rows

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self._rows


class _FakeConnection:
    def __init__(self, rows: list[tuple[Any, ...]] | None = None) -> None:
        self.statements: list[tuple[str, tuple[Any, ...]]] = []
        self.committed = False
        self._rows = rows or []

    def execute(self, query: str, params: tuple[Any, ...] = ()) -> _FakeCursor:
        self.statements.append((query, params))
        return _FakeCursor(self._rows)

    def commit(self) -> None:
        self.committed = True


def test_save_conversation_inserts_and_commits() -> None:
    connection = _FakeConnection()

    save_conversation(
        connection,
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

    query, params = connection.statements[0]
    assert "INSERT INTO app.conversation_history" in query
    assert params[0] == "kim.quality"
    assert params[1] == "정가 알려줘"
    assert params[2] == "1200원입니다"
    assert '"listprice": 1200' in params[5]
    assert params[6] is None
    assert connection.committed is True


def test_list_history_scopes_to_own_rows_for_non_admin() -> None:
    connection = _FakeConnection(
        rows=[
            (1, "kim.quality", "q", "a", None, None, None, None, datetime(2026, 1, 1))
        ]
    )

    result = list_history(connection, CurrentUser(username="kim.quality", role="user"))

    query, params = connection.statements[0]
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


def test_list_history_returns_all_rows_for_admin() -> None:
    connection = _FakeConnection(rows=[])

    list_history(connection, CurrentUser(username="park.admin", role="admin"))

    query, params = connection.statements[0]
    assert "WHERE" not in query
    assert params == ()
