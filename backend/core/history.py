"""대화기록 저장·조회를 다룬다."""

import json
from types import TracebackType
from typing import Any, Protocol

from core.auth import CurrentUser


class Cursor(Protocol):
    rowcount: int

    async def fetchall(self) -> list[tuple[Any, ...]]: ...
    async def fetchone(self) -> tuple[Any, ...] | None: ...


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
    # 테스트 fake pool(각각 __aenter__/__aexit__를 직접 구현)은 이 Protocol과
    # 정확히 구조가 맞아 여기서 검증된다. 반면 실제 psycopg_pool.AsyncConnectionPool
    # 의 connection()은 @asynccontextmanager로 구현돼 있는데, mypy(v2.3.0
    # 기준, 실측 확인)는 @asynccontextmanager가 반환하는
    # contextlib._AsyncGeneratorContextManager를 이 Protocol은 물론
    # AbstractAsyncContextManager[Connection, None]에도 구조적으로 맞는다고
    # 인정하지 않는다(Any/구체 타입 무관하게 재현됨 - 커스텀 프로토콜 문제가
    # 아니라 @asynccontextmanager 자체에 대한 mypy 한계). get_pool()/
    # get_write_pool() 결과를 넘기는 호출부 3곳에 그래서 type: ignore를 둔다.
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
    visualization: dict | None = None,
) -> int:
    async with pool.connection() as conn:
        cursor = await conn.execute(
            "INSERT INTO app.conversation_history "
            "(username, query, final_answer, sql_query, cypher_query, sql_result, graph_result, visualization) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
            (
                username,
                query,
                final_answer,
                sql_query,
                cypher_query,
                json.dumps(sql_result) if sql_result is not None else None,
                json.dumps(graph_result) if graph_result is not None else None,
                json.dumps(visualization) if visualization is not None else None,
            ),
        )
        row = await cursor.fetchone()
        await conn.commit()
        if row is None:
            raise RuntimeError("conversation insert did not return an id")
        return int(row[0])


async def delete_conversation(pool: Pool, user: CurrentUser, history_id: int) -> bool:
    """admin이면 아무 기록이나, 아니면 본인 기록만 id로 삭제한다. 실제로
    삭제된 행이 있으면 True, 없으면(다른 사용자 소유 포함) False를 반환한다."""
    base_query = "DELETE FROM app.conversation_history WHERE id = %s"
    async with pool.connection() as conn:
        if user.role == "admin":
            cursor = await conn.execute(base_query, (history_id,))
        else:
            cursor = await conn.execute(
                base_query + " AND username = %s", (history_id, user.username)
            )
        await conn.commit()
    return cursor.rowcount > 0


async def list_history(pool: Pool, user: CurrentUser) -> list[dict]:
    """admin이면 전체, 아니면 본인 기록만 최신순으로 반환한다."""
    base_query = (
        "SELECT id, username, query, final_answer, sql_query, cypher_query, "
        "sql_result, graph_result, visualization, created_at "
        "FROM app.conversation_history"
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
            "visualization": row[8],
            "created_at": row[9].isoformat(),
        }
        for row in rows
    ]
