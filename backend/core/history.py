"""대화기록 저장·조회를 다룬다."""

import json
from typing import Any

from core.auth import CurrentUser


async def save_conversation(
    pool: Any,
    username: str,
    query: str,
    final_answer: str | None,
    sql_query: str | None,
    cypher_query: str | None,
    sql_result: dict | None,
    graph_result: dict | None,
) -> None:
    async with pool.connection() as conn:
        await conn.execute(
            "INSERT INTO app.conversation_history "
            "(username, query, final_answer, sql_query, cypher_query, sql_result, graph_result) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (
                username,
                query,
                final_answer,
                sql_query,
                cypher_query,
                json.dumps(sql_result) if sql_result is not None else None,
                json.dumps(graph_result) if graph_result is not None else None,
            ),
        )
        await conn.commit()


async def list_history(pool: Any, user: CurrentUser) -> list[dict]:
    """admin이면 전체, 아니면 본인 기록만 최신순으로 반환한다."""
    base_query = (
        "SELECT id, username, query, final_answer, sql_query, cypher_query, "
        "sql_result, graph_result, created_at FROM app.conversation_history"
    )
    async with pool.connection() as conn:
        if user.role == "admin":
            cursor = await conn.execute(base_query + " ORDER BY created_at DESC")
        else:
            cursor = await conn.execute(
                base_query + " WHERE username = %s ORDER BY created_at DESC",
                (user.username,),
            )
        rows = await cursor.fetchall()
    return [
        {
            "id": row[0],
            "username": row[1],
            "query": row[2],
            "final_answer": row[3],
            "sql_query": row[4],
            "cypher_query": row[5],
            "sql_result": row[6],
            "graph_result": row[7],
            "created_at": row[8].isoformat(),
        }
        for row in rows
    ]
