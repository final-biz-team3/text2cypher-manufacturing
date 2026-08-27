"""SQL Agent가 생성한 읽기 전용 쿼리를 PostgreSQL 커넥션 풀로 실행한다."""

import os
from typing import Any

import psycopg

from core.postgres import get_pool


async def execute_sql_with_pool(
    pool: Any, sql: str, *, row_limit: int
) -> list[dict[str, Any]]:
    """풀에서 커넥션을 빌려 sql을 실행하고, 결과를 다 읽은 뒤 rollback한다.
    예외는 여기서 감싸지 않고 원본 타입 그대로 전파한다(retry_agent.py의
    재시도 분류가 원본 예외 타입에 의존하기 때문)."""
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
            try:
                await cur.execute(sql)
                rows = await cur.fetchmany(row_limit + 1)
            finally:
                await conn.rollback()
    return rows[:row_limit]


async def execute_sql(sql: str) -> list[dict[str, Any]]:
    """graph.py가 주입하는 기본 execute_sql - 앱 전역 풀과 SQL_ROW_LIMIT을 사용한다."""
    row_limit = int(os.getenv("SQL_ROW_LIMIT", "200"))
    return await execute_sql_with_pool(get_pool(), sql, row_limit=row_limit)
