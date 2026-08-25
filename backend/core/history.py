"""대화기록 저장·조회를 다룬다."""

import json
from typing import Any

from core.auth import CurrentUser


def save_conversation(
    connection: Any,
    username: str,
    query: str,
    final_answer: str | None,
    sql_query: str | None,
    cypher_query: str | None,
    sql_result: dict | None,
    graph_result: dict | None,
) -> None:
    connection.execute(
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
    connection.commit()


def list_history(connection: Any, user: CurrentUser) -> list[dict]:
    """admin이면 전체, 아니면 본인 기록만 최신순으로 반환한다."""
    query = (
        "SELECT id, username, query, final_answer, sql_query, cypher_query, "
        "sql_result, graph_result, created_at FROM app.conversation_history"
    )
    if user.role == "admin":
        cursor = connection.execute(query + " ORDER BY created_at DESC")
    else:
        cursor = connection.execute(
            query + " WHERE username = %s ORDER BY created_at DESC", (user.username,)
        )
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
        for row in cursor.fetchall()
    ]
