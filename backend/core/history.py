"""대화기록 저장·조회를 다룬다."""

import json
from types import TracebackType
from typing import Any, Protocol

from core.auth import CurrentUser


class Cursor(Protocol):
    async def fetchall(self) -> list[tuple[Any, ...]]: ...


class Connection(Protocol):
    """실제 psycopg AsyncConnection과 테스트용 fake가 공통으로 만족하는
    인터페이스. get_pool()/get_write_pool()이 async 풀로 바뀌면서
    `async with pool.connection() as conn:` 패턴을 쓰게 됐으므로, 이전
    (동기) Protocol과 달리 각 메서드가 코루틴을 반환한다."""

    async def execute(self, query: str, params: tuple[Any, ...] = ()) -> Cursor: ...
    async def commit(self) -> None: ...


class ConnectionContext(Protocol):
    async def __aenter__(self) -> Connection: ...
    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None: ...


class Pool(Protocol):
    def connection(self, timeout: float | None = None) -> ConnectionContext: ...


async def save_conversation(
    pool: Pool,
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


async def list_history(pool: Pool, user: CurrentUser) -> list[dict]:
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
