"""실제 PostgreSQL에서 SQL Agent의 42P10 self-correction을 검증한다."""

from collections.abc import AsyncIterator
from pathlib import Path

import psycopg
import pytest
import pytest_asyncio
from dotenv import load_dotenv
from psycopg_pool import AsyncConnectionPool

from agents.sql.schema.loader import load_sql_schema
from agents.sql.schema.serializer import serialize_sql_schema
from core.postgres import configure_connection, postgres_conninfo
from orchestrator.execution.result import QueryResultBatch
from orchestrator.execution.sql_executor import execute_sql_with_pool
from orchestrator.subgraphs.sql_agent import make_sql_agent_subgraph
from tests.mocks.openai import MockOpenAIClient, make_content_response

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]

PROJECT_ROOT = Path(__file__).resolve().parents[3]
SQL_SCHEMA = load_sql_schema(PROJECT_ROOT / "schema" / "sql_schema.yaml")
SQL_SCHEMA_TEXT = serialize_sql_schema(SQL_SCHEMA)

FIRST_SQL = """SELECT DISTINCT productid AS "productId"
FROM (VALUES (2, 'B'), (1, 'A')) AS product(productid, name)
ORDER BY name"""
CORRECTED_SQL = """SELECT DISTINCT
    productid AS "productId",
    name AS "productName"
FROM (VALUES (2, 'B'), (1, 'A')) AS product(productid, name)
ORDER BY name"""
POSTGRES_ERROR = "for SELECT DISTINCT, ORDER BY expressions must appear in select list"


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def postgres_pool() -> AsyncIterator[AsyncConnectionPool]:
    """운영 설정과 동일하게 구성한 독립적인 읽기 전용 풀을 연다."""
    load_dotenv(PROJECT_ROOT / ".env")
    pool = AsyncConnectionPool(
        postgres_conninfo(),
        configure=configure_connection,
        open=False,
        min_size=1,
        max_size=1,
    )
    await pool.open(wait=True)
    try:
        yield pool
    finally:
        await pool.close()


async def test_sql_agent_recovers_from_postgresql_42p10(
    postgres_pool: AsyncConnectionPool,
) -> None:
    """실제 42P10 오류를 피드백해 보정 SQL을 생성하고 재실행한다."""
    openai_client = MockOpenAIClient(
        make_content_response(FIRST_SQL),
        make_content_response(CORRECTED_SQL),
    )
    executor_calls: list[str] = []
    executor_errors: list[psycopg.Error] = []

    async def execute_sql(sql: str) -> QueryResultBatch:
        executor_calls.append(sql)
        try:
            return await execute_sql_with_pool(postgres_pool, sql, row_limit=10)
        except psycopg.Error as exc:
            executor_errors.append(exc)
            raise

    subgraph = make_sql_agent_subgraph(
        openai_client,
        execute_sql=execute_sql,
        sql_schema=SQL_SCHEMA,
    )

    result = await subgraph.ainvoke(
        {
            "query": "제품 ID와 이름을 이름순으로 보여줘.",
            "entity": None,
            "schema": SQL_SCHEMA_TEXT,
            "messages": [],
            "result": None,
            "error": None,
        }
    )

    assert len(executor_errors) == 1
    actual_error = executor_errors[0]
    assert isinstance(actual_error, psycopg.errors.InvalidColumnReference)
    assert actual_error.sqlstate == "42P10"
    assert actual_error.diag.message_primary == POSTGRES_ERROR

    assert len(openai_client.calls) == 2
    assert executor_calls == [FIRST_SQL, CORRECTED_SQL]
    retry_system_prompt = openai_client.calls[1]["messages"][0]["content"]
    assert FIRST_SQL in retry_system_prompt
    assert str(actual_error) in retry_system_prompt

    assert result["attempts"] == [
        {"query": FIRST_SQL, "error": str(actual_error)},
        {"query": CORRECTED_SQL, "error": None},
    ]
    assert result["retryDiagnostics"] == [
        {
            "stage": "execution",
            "reasonCode": "QUERY_EXECUTION_FAILED",
            "errorType": "InvalidColumnReference",
            "sqlstate": "42P10",
            "recovered": True,
        }
    ]
    assert result["result"] == [
        {"productId": 1, "productName": "A"},
        {"productId": 2, "productName": "B"},
    ]
    assert result["error"] is None
    assert result["failure"] is None
    assert result["truncated"] is False
    assert result["messages"][-1] == {
        "role": "assistant",
        "content": CORRECTED_SQL,
    }
