from typing import Any

import core.postgres as postgres


class _Connection:
    closed = False


def test_connection_enforces_read_only_session(
    monkeypatch,
) -> None:
    captured: dict[str, Any] = {}
    connection = _Connection()

    def connect(**kwargs: Any) -> _Connection:
        captured.update(kwargs)
        return connection

    monkeypatch.setattr(postgres, "_connection", None)
    monkeypatch.setattr(postgres.psycopg, "connect", connect)
    monkeypatch.setenv("POSTGRES_APP_USER", "app_reader")
    monkeypatch.setenv("POSTGRES_APP_PASSWORD", "reader_password")

    assert postgres.get_connection() is connection
    assert captured["user"] == "app_reader"
    assert captured["password"] == "reader_password"
    assert captured["options"] == "-c default_transaction_read_only=on"


def test_history_connection_is_separate_and_does_not_force_read_only(
    monkeypatch,
) -> None:
    captured: list[dict[str, Any]] = []

    def connect(**kwargs: Any) -> _Connection:
        captured.append(kwargs)
        return _Connection()

    monkeypatch.setattr(postgres, "_history_write_connection", None)
    monkeypatch.setattr(postgres.psycopg, "connect", connect)
    monkeypatch.setenv("POSTGRES_APP_USER", "app_reader")
    monkeypatch.setenv("POSTGRES_APP_PASSWORD", "reader_password")

    postgres.get_history_write_connection()

    assert captured[0]["user"] == "app_reader"
    assert captured[0]["password"] == "reader_password"
    assert captured[0]["options"] == ""
