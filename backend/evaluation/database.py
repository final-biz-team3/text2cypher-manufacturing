"""평가 전용 PostgreSQL/Neo4j 읽기 트랜잭션 실행기."""

import os
from collections.abc import Mapping
from typing import Any

import psycopg
from neo4j import Driver as Neo4jDriver
from neo4j import GraphDatabase, unit_of_work
from psycopg import Connection

from evaluation.errors import (
    ConfigurationError,
    InfrastructureError,
    ResultContractError,
)
from evaluation.safety import validate_read_only_cypher, validate_read_only_sql


class ReadOnlyDatabaseExecutor:
    """timeout과 행 제한을 DB 트랜잭션 수준에서도 강제한다."""

    def __init__(
        self,
        postgres: Connection[Any],
        neo4j: Neo4jDriver,
        *,
        neo4j_database: str | None = None,
        timeout_ms: int = 3000,
    ) -> None:
        self.postgres = postgres
        self.neo4j = neo4j
        self.neo4j_database = neo4j_database or None
        self.timeout_ms = timeout_ms

    @classmethod
    def from_environment(cls, *, timeout_ms: int = 3000) -> "ReadOnlyDatabaseExecutor":
        required = (
            "POSTGRES_HOST",
            "POSTGRES_PORT",
            "POSTGRES_DB",
            "POSTGRES_USER",
            "POSTGRES_PASSWORD",
            "NEO4J_URI",
            "NEO4J_USER",
            "NEO4J_PASSWORD",
        )
        missing = [name for name in required if not os.getenv(name)]
        if missing:
            raise ConfigurationError(
                "DB 환경변수가 없습니다: " + ", ".join(sorted(missing))
            )
        try:
            postgres = psycopg.connect(
                host=os.environ["POSTGRES_HOST"],
                port=os.environ["POSTGRES_PORT"],
                dbname=os.environ["POSTGRES_DB"],
                user=os.environ["POSTGRES_USER"],
                password=os.environ["POSTGRES_PASSWORD"],
                autocommit=True,
                connect_timeout=10,
            )
            neo4j = GraphDatabase.driver(
                os.environ["NEO4J_URI"],
                auth=(os.environ["NEO4J_USER"], os.environ["NEO4J_PASSWORD"]),
                connection_timeout=10,
            )
            neo4j.verify_connectivity()
        except Exception as exc:
            if "postgres" in locals():
                postgres.close()
            raise InfrastructureError(f"DB 연결 실패: {exc}") from exc
        return cls(
            postgres,
            neo4j,
            neo4j_database=os.getenv("NEO4J_DATABASE"),
            timeout_ms=timeout_ms,
        )

    def close(self) -> None:
        self.postgres.close()
        self.neo4j.close()

    def execute_sql(
        self,
        query: str,
        parameters: Mapping[str, Any] | None = None,
        *,
        max_rows: int,
    ) -> list[dict[str, Any]]:
        validate_read_only_sql(query)
        with self.postgres.transaction():
            self.postgres.execute("SET TRANSACTION READ ONLY")
            self.postgres.execute(
                "SELECT set_config('statement_timeout', %s, true)",
                (str(self.timeout_ms),),
            )
            cursor = self.postgres.execute(query, parameters or {})
            if cursor.description is None:
                raise ResultContractError("SQL이 결과 컬럼을 반환하지 않았습니다.")
            columns = [column.name for column in cursor.description]
            rows = cursor.fetchmany(max_rows + 1)
            if len(rows) > max_rows:
                raise ResultContractError(
                    f"SQL 결과가 최대 행 수 {max_rows}를 초과했습니다."
                )
            return [dict(zip(columns, row, strict=True)) for row in rows]

    def execute_cypher(
        self,
        query: str,
        parameters: Mapping[str, Any] | None = None,
        *,
        max_rows: int,
    ) -> list[dict[str, Any]]:
        validate_read_only_cypher(query)

        def read(transaction: Any) -> list[dict[str, Any]]:
            result = transaction.run(query, parameters or {})
            records: list[dict[str, Any]] = []
            for record in result:
                if len(records) >= max_rows:
                    raise ResultContractError(
                        f"Cypher 결과가 최대 행 수 {max_rows}를 초과했습니다."
                    )
                records.append(record.data())
            return records

        with self.neo4j.session(database=self.neo4j_database) as session:
            timed_read = unit_of_work(timeout=self.timeout_ms / 1000)(read)
            return session.execute_read(timed_read)

    def sync_run_ids(self) -> list[str | None]:
        rows = self.execute_cypher(
            "MATCH (n) RETURN DISTINCT n.syncRunId AS syncRunId ORDER BY syncRunId",
            max_rows=10,
        )
        return [row["syncRunId"] for row in rows]
