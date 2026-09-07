"""Existing read-only execution plus server metadata; EXPLAIN is not semantics."""

import os
from typing import Any

from neo4j import unit_of_work

from orchestrator.execution.cypher_executor import execute_cypher, get_reader_driver


async def sql_candidate(
    pool: Any, sql: str, *, row_limit: int, evidence: dict[str, Any]
) -> Any:
    async with pool.connection() as conn:
        try:
            async with conn.cursor() as cursor:
                await cursor.execute("EXPLAIN (FORMAT JSON) " + sql)
                await cursor.fetchall()
        finally:
            await conn.rollback()
    # Same read-only pool and row limiter as production.
    async with pool.connection() as conn:
        import psycopg

        from orchestrator.execution.result import make_batch

        try:
            async with conn.cursor(row_factory=psycopg.rows.dict_row) as cursor:
                await cursor.execute(sql)
                columns = [column.name for column in cursor.description or []]
                rows = await cursor.fetchmany(row_limit + 1)
                batch = make_batch(rows, row_limit)
        finally:
            await conn.rollback()
    evidence["sql"] = {"columns": columns, "plan_checked": True, "query": sql}
    return batch


async def graph_candidate(cypher: str, *, evidence: dict[str, Any]) -> Any:
    driver = get_reader_driver()

    async def explain(tx: Any) -> list[str]:
        result = await tx.run("EXPLAIN " + cypher)
        columns = list(result.keys())
        await result.consume()
        return columns

    async with driver.session() as session:
        columns = await session.execute_read(
            unit_of_work(timeout=float(os.getenv("NEO4J_QUERY_TIMEOUT_SEC", "10")))(
                explain
            )
        )
    batch = await execute_cypher(cypher)
    evidence["graph"] = {"columns": columns, "plan_checked": True, "query": cypher}
    return batch
