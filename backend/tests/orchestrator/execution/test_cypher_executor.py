"""execute_cypher가 result.data() 형태 반환·timeout 전달·행 상한·예외 전파를 지키는지 검증한다."""

import pytest
from neo4j.exceptions import AuthError, Forbidden

from orchestrator.execution.cypher_executor import (
    execute_cypher_with_driver,
    verify_reader_is_read_only,
)
from tests.mocks.neo4j import MockAsyncNeo4jDriver


async def test_execute_cypher_returns_data_records() -> None:
    """session.execute_read(Query(timeout=...)) -> result.data() 결과를 그대로 반환한다."""
    driver = MockAsyncNeo4jDriver(records=[{"n": {"productId": 492}}])

    batch = await execute_cypher_with_driver(
        driver, "MATCH (n:Product) RETURN n", timeout_sec=5.0, row_limit=10
    )

    assert batch == {"rows": [{"n": {"productId": 492}}], "truncated": False}


async def test_execute_cypher_truncates_to_row_limit() -> None:
    """result.fetch(row_limit+1)이 row_limit보다 많이 돌려줘도 row_limit개로
    자른다(SQL 쪽 execute_sql_with_pool의 fetchmany(N+1) 패턴과 동일)."""
    driver = MockAsyncNeo4jDriver(records=[{"id": i} for i in range(10)])

    batch = await execute_cypher_with_driver(
        driver, "MATCH (n) RETURN n", timeout_sec=5.0, row_limit=3
    )

    assert len(batch["rows"]) == 3
    assert batch["truncated"] is True


async def test_execute_cypher_passes_timeout_to_query() -> None:
    """neo4j.unit_of_work(timeout=...)로 트랜잭션 함수에 timeout_sec을 건다.
    (tx.run(Query(timeout=...))은 관리형 트랜잭션에서 지원되지 않아 실제로
    "Query object is only supported for session.run"으로 실패함을 실측으로
    확인했다 - unit_of_work 데코레이터가 올바른 방법이다.)"""
    driver = MockAsyncNeo4jDriver(records=[])

    await execute_cypher_with_driver(driver, "RETURN 1", timeout_sec=2.5, row_limit=10)

    assert driver.last_timeout == 2.5
    assert driver.last_transaction is not None
    assert driver.last_transaction.ran_query_text == "RETURN 1"


async def test_execute_cypher_propagates_original_exception_without_wrapping() -> None:
    """실행 중 예외가 나면 커스텀 예외로 감싸지 않고 원본 타입 그대로 전파한다."""
    driver = MockAsyncNeo4jDriver(error=AuthError("unauthorized"))

    with pytest.raises(AuthError):
        await execute_cypher_with_driver(
            driver, "RETURN 1", timeout_sec=5.0, row_limit=10
        )


async def test_verify_reader_is_read_only_passes_when_write_is_forbidden() -> None:
    """reader role이 실제로 쓰기를 거부하면(Forbidden) 조용히 통과한다."""
    driver = MockAsyncNeo4jDriver(write_error=Forbidden("not allowed"))

    await verify_reader_is_read_only(driver)


async def test_verify_reader_is_read_only_raises_when_write_unexpectedly_succeeds() -> (
    None
):
    """쓰기가 거부되지 않으면(계정이 실제로는 관리자 등) 명확하게 실패시킨다."""
    driver = MockAsyncNeo4jDriver()

    with pytest.raises(RuntimeError, match="쓰기를 거부하지 않습니다"):
        await verify_reader_is_read_only(driver)
