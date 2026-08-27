from typing import Any

import psycopg

import core.postgres as postgres


def test_application_conninfo_uses_limited_role(monkeypatch: Any) -> None:
    monkeypatch.setenv("POSTGRES_APP_USER", "app_reader")
    monkeypatch.setenv("POSTGRES_APP_PASSWORD", "reader_password")

    values = psycopg.conninfo.conninfo_to_dict(postgres.postgres_app_conninfo())

    assert values["user"] == "app_reader"
    assert values["password"] == "reader_password"
    assert postgres.postgres_conninfo() == postgres.postgres_app_conninfo()


def test_admin_conninfo_is_reserved_for_bootstrap(monkeypatch: Any) -> None:
    monkeypatch.setenv("POSTGRES_USER", "database_admin")
    monkeypatch.setenv("POSTGRES_PASSWORD", "admin_password")

    values = psycopg.conninfo.conninfo_to_dict(postgres.postgres_admin_conninfo())

    assert values["user"] == "database_admin"
    assert values["password"] == "admin_password"


class _ConfiguredConnection:
    def __init__(self) -> None:
        self.read_only: bool | None = None
        self.executed: list[str] = []
        self.committed = False

    async def set_read_only(self, value: bool) -> None:
        self.read_only = value

    async def execute(self, query: str) -> None:
        self.executed.append(query)

    async def commit(self) -> None:
        self.committed = True


async def test_read_pool_connection_is_configured_read_only(monkeypatch: Any) -> None:
    monkeypatch.setenv("SQL_STATEMENT_TIMEOUT_MS", "4321")
    connection = _ConfiguredConnection()

    await postgres.configure_connection(connection)  # type: ignore[arg-type]

    assert connection.read_only is True
    assert connection.executed == ["SET statement_timeout = '4321ms'"]
    assert connection.committed is True


async def test_history_pool_does_not_enable_read_only(monkeypatch: Any) -> None:
    monkeypatch.setenv("SQL_STATEMENT_TIMEOUT_MS", "4321")
    connection = _ConfiguredConnection()

    await postgres.configure_write_connection(connection)  # type: ignore[arg-type]

    assert connection.read_only is None
    assert connection.executed == ["SET statement_timeout = '4321ms'"]
    assert connection.committed is True
