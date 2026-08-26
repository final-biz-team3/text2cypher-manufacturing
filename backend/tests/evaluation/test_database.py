from types import SimpleNamespace
from typing import Any

import pytest

import evaluation.database as database_module
from evaluation.database import ReadOnlyDatabaseExecutor
from evaluation.errors import ResultContractError


class _Transaction:
    def __enter__(self) -> None:
        return None

    def __exit__(self, *args: Any) -> None:
        return None


class _Cursor:
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self.description = [SimpleNamespace(name="value")]
        self._rows = rows

    def fetchmany(self, size: int) -> list[tuple[Any, ...]]:
        return self._rows[:size]


class _Postgres:
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self.rows = rows
        self.queries: list[str] = []

    def transaction(self) -> _Transaction:
        return _Transaction()

    def execute(self, query: str, parameters: Any = None) -> _Cursor:
        self.queries.append(query)
        return _Cursor(self.rows)


class _Neo4jRecord:
    def __init__(self, value: int) -> None:
        self.value = value

    def data(self) -> dict[str, int]:
        return {"value": self.value}


class _Neo4jTransaction:
    def __init__(self, values: list[int]) -> None:
        self.values = values

    def run(self, query: str, parameters: Any) -> list[_Neo4jRecord]:
        return [_Neo4jRecord(value) for value in self.values]


class _Neo4jSession:
    def __init__(self, values: list[int]) -> None:
        self.values = values
        self.used_execute_read = False

    def __enter__(self) -> "_Neo4jSession":
        return self

    def __exit__(self, *args: Any) -> None:
        return None

    def execute_read(self, function: Any) -> list[dict[str, Any]]:
        self.used_execute_read = True
        return function(_Neo4jTransaction(self.values))


class _Neo4j:
    def __init__(self, values: list[int]) -> None:
        self.session_instance = _Neo4jSession(values)

    def session(self, **kwargs: Any) -> _Neo4jSession:
        return self.session_instance


def test_sql_uses_read_only_transaction_timeout_and_max_rows() -> None:
    postgres = _Postgres([(1,), (2,), (3,)])
    executor = ReadOnlyDatabaseExecutor(postgres, _Neo4j([]), timeout_ms=1234)  # type: ignore[arg-type]

    with pytest.raises(ResultContractError, match="최대 행 수 2"):
        executor.execute_sql("SELECT value FROM fixture", max_rows=2)

    assert "SET TRANSACTION READ ONLY" in postgres.queries
    assert any("statement_timeout" in query for query in postgres.queries)


def test_cypher_uses_read_transaction_timeout_and_max_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, float] = {}

    def fake_unit_of_work(*, timeout: float) -> Any:
        captured["timeout"] = timeout
        return lambda function: function

    monkeypatch.setattr(database_module, "unit_of_work", fake_unit_of_work)
    neo4j = _Neo4j([1, 2])
    executor = ReadOnlyDatabaseExecutor(_Postgres([]), neo4j, timeout_ms=2500)  # type: ignore[arg-type]

    with pytest.raises(ResultContractError, match="최대 행 수 1"):
        executor.execute_cypher("MATCH (n) RETURN n.value AS value", max_rows=1)

    assert neo4j.session_instance.used_execute_read is True
    assert captured["timeout"] == 2.5
