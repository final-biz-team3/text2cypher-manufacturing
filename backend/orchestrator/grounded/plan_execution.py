"""Read-only, parameterized execution keyed by source step rather than tool."""

import os
from time import perf_counter
from typing import Any

import neo4j
import psycopg
from psycopg.types.json import Jsonb

from orchestrator.execution.cypher_executor import get_reader_driver
from orchestrator.grounded.plan_engine import PlanError
from orchestrator.grounded.plan_models import SourceStep, StepResult


def make_step_executor(
    pool: Any, sql_guard: Any, graph_guard: Any, cap: int = 200
) -> Any:
    async def execute(step: SourceStep, params: dict[str, Any]) -> StepResult:
        start = perf_counter()
        guard = sql_guard if step.tool == "sql" else graph_guard
        if not guard(step.query).allowed:
            raise PlanError("Source query rejected by read-only/schema guard")
        if step.tool == "sql":
            bound = {
                k: Jsonb(v) if isinstance(v, list) else v for k, v in params.items()
            }
            async with pool.connection() as conn:
                try:
                    async with conn.cursor(row_factory=psycopg.rows.dict_row) as cursor:
                        await cursor.execute(
                            "EXPLAIN (FORMAT JSON) " + step.query, bound or None
                        )
                        await cursor.fetchall()
                        await cursor.execute(step.query, bound or None)
                        columns = [column.name for column in cursor.description or []]
                        rows = await cursor.fetchmany(cap + 1)
                finally:
                    await conn.rollback()
        else:

            async def run(tx: Any) -> tuple[list[str], list[dict[str, Any]]]:
                explanation = await tx.run("EXPLAIN " + step.query, params)
                await explanation.consume()
                result = await tx.run(step.query, params)
                names = list(result.keys())
                records = await result.fetch(cap + 1)
                return names, [record.data() for record in records]

            async with get_reader_driver().session() as session:
                columns, rows = await session.execute_read(
                    neo4j.unit_of_work(
                        timeout=float(os.getenv("NEO4J_QUERY_TIMEOUT_SEC", "10"))
                    )(run)
                )
        if len(columns) != len(set(columns)):
            raise PlanError("Duplicate source columns lose row-field provenance")
        return StepResult(
            step_id=step.id,
            columns=columns,
            rows=rows[:cap],
            truncated=len(rows) > cap,
            query=step.query,
            elapsed_ms=(perf_counter() - start) * 1000,
        )

    return execute
