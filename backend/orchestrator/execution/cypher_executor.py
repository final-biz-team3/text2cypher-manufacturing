"""Cypher Agent가 생성한 읽기 전용 쿼리를 Neo4j reader 계정으로 실행한다."""

import os
from typing import Any

import neo4j
from neo4j import AsyncDriver, AsyncGraphDatabase

_reader_driver: AsyncDriver | None = None


def get_reader_driver() -> AsyncDriver:
    """execute_cypher 전용 reader 계정 드라이버 싱글턴.
    core/neo4j.py의 관리자 계정 드라이버(헬스체크·스키마용)와는 별개다."""
    global _reader_driver
    if _reader_driver is None:
        _reader_driver = AsyncGraphDatabase.driver(
            os.environ["NEO4J_URI"],
            auth=(
                os.environ["NEO4J_READER_USER"],
                os.environ["NEO4J_READER_PASSWORD"],
            ),
        )
    return _reader_driver


async def close_reader_driver() -> None:
    global _reader_driver
    if _reader_driver is not None:
        await _reader_driver.close()
        _reader_driver = None


async def execute_cypher_with_driver(
    driver: Any, cypher: str, *, timeout_sec: float, row_limit: int
) -> list[dict[str, Any]]:
    """세션을 열고 읽기 트랜잭션으로 cypher를 실행한 뒤 dict 리스트를 반환한다.
    타임아웃은 tx.run(neo4j.Query(...))으로 걸 수 없다 - 실제로 돌려보면
    "Query object is only supported for session.run"으로 즉시 실패한다(관리형
    트랜잭션인 execute_read 안에서는 Query 객체를 못 쓴다). 대신 driver가
    execute_read 호출 시 transaction_function.timeout 속성을 읽어 트랜잭션
    자체에 타임아웃을 거는 neo4j.unit_of_work(timeout=...) 데코레이터를 쓴다.

    result.data()로 한 번에 다 읽지 않고 result.fetch(row_limit+1)로 상한을
    넘는지 확인 후 자른다 - execute_sql_with_pool의 fetchmany(N+1) 패턴과
    동일하다. LLM이 만든 Cypher에 LIMIT이 없어도(SQL과 달리 SQL_ROW_LIMIT
    같은 명시적 장치가 없었다) 결과 폭주를 막는다.

    예외는 여기서 감싸지 않고 원본 타입 그대로 전파한다."""

    async def _run(tx: Any) -> list[dict[str, Any]]:
        result = await tx.run(cypher)
        records = await result.fetch(row_limit + 1)
        return [record.data() for record in records[:row_limit]]

    run_with_timeout = neo4j.unit_of_work(timeout=timeout_sec)(_run)

    async with driver.session() as session:
        return await session.execute_read(run_with_timeout)


async def execute_cypher(cypher: str) -> list[dict[str, Any]]:
    """graph.py가 주입하는 기본 execute_cypher - reader 드라이버,
    NEO4J_QUERY_TIMEOUT_SEC, SQL_ROW_LIMIT을 사용한다. 행 상한은 SQL과
    같은 값을 공유한다 - "결과 행 폭주를 막는다"는 목적이 DB 종류와
    무관하게 동일해서, 별도 환경변수를 새로 만들지 않았다."""
    timeout_sec = float(os.getenv("NEO4J_QUERY_TIMEOUT_SEC", "10"))
    row_limit = int(os.getenv("SQL_ROW_LIMIT", "200"))
    return await execute_cypher_with_driver(
        get_reader_driver(), cypher, timeout_sec=timeout_sec, row_limit=row_limit
    )
