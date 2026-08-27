"""Cypher Agent가 생성한 읽기 전용 쿼리를 Neo4j reader 계정으로 실행한다."""

import os
from typing import Any

import neo4j
from neo4j import AsyncDriver
from neo4j.exceptions import Forbidden

from core.neo4j import build_driver

_reader_driver: AsyncDriver | None = None

_WRITE_PROBE_LABEL = "__reader_write_probe__"


def get_reader_driver() -> AsyncDriver:
    """execute_cypher 전용 reader 계정 드라이버 싱글턴.
    core/neo4j.py의 관리자 계정 드라이버(헬스체크·스키마용)와는 별개다.
    드라이버 생성 로직(및 NEO4J_URI 필수 요구)은 core.neo4j.build_driver를
    공유한다 - 예전엔 이 함수가 따로 AsyncGraphDatabase.driver(...)를
    호출하고 있어서 관리자 드라이버와 요구 조건이 미묘하게 달랐다."""
    global _reader_driver
    if _reader_driver is None:
        _reader_driver = build_driver(
            os.environ["NEO4J_READER_USER"],
            os.environ["NEO4J_READER_PASSWORD"],
        )
    return _reader_driver


async def close_reader_driver() -> None:
    global _reader_driver
    if _reader_driver is not None:
        await _reader_driver.close()
        _reader_driver = None


async def verify_reader_is_read_only(driver: Any) -> None:
    """reader 계정이 실제로 쓰기를 거부하는지 시작 시 한 번 확인한다.

    execute_cypher()는 항상 session.execute_read()만 쓰므로 access_mode
    자체가 서버에서 쓰기를 막아주지만(계정 권한과 무관하게), 이건 "이
    코드가 항상 execute_read만 호출한다"는 전제에 기대는 것이다. .env에
    NEO4J_READER_USER/PASSWORD를 실수로 관리자 계정 값으로 넣으면
    이 전제가 깨졌을 때(예: 나중에 다른 코드가 execute_write를 호출) 진짜
    쓰기가 나갈 수 있으므로, 계정 자체의 role 권한도 시작 시점에 직접
    검증한다. reader role은 새 라벨 생성 권한이 없어 Forbidden이 난다."""

    async def _write(tx: Any) -> None:
        await tx.run(f"CREATE (:{_WRITE_PROBE_LABEL})")

    async def _cleanup(tx: Any) -> None:
        await tx.run(f"MATCH (n:{_WRITE_PROBE_LABEL}) DELETE n")

    async with driver.session() as session:
        try:
            await session.execute_write(_write)
        except Forbidden:
            return
        # 여기 도달했다는 건 계정이 실제로 쓰기 권한을 갖고 있다는 뜻이라
        # 방금 만든 노드부터 지운 뒤 명확하게 실패시킨다.
        await session.execute_write(_cleanup)
        raise RuntimeError(
            "NEO4J_READER_USER 계정이 쓰기를 거부하지 않습니다 - "
            "reader role이 아닌(예: 관리자) 계정이 설정됐을 수 있습니다."
        )


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
