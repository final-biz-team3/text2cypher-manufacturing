"""execute_cypher가 result.data() 형태 반환·timeout 전달·예외 전파를 지키는지 검증한다."""

import pytest
from neo4j.exceptions import AuthError

from orchestrator.execution.cypher_executor import execute_cypher_with_driver
from tests.mocks.neo4j import MockAsyncNeo4jDriver


async def test_execute_cypher_returns_data_records() -> None:
    """session.execute_read(Query(timeout=...)) -> result.data() 결과를 그대로 반환한다."""
    driver = MockAsyncNeo4jDriver(records=[{"n": {"productId": 492}}])

    rows = await execute_cypher_with_driver(
        driver, "MATCH (n:Product) RETURN n", timeout_sec=5.0
    )

    assert rows == [{"n": {"productId": 492}}]


async def test_execute_cypher_passes_timeout_to_query() -> None:
    """neo4j.unit_of_work(timeout=...)로 트랜잭션 함수에 timeout_sec을 건다.
    (tx.run(Query(timeout=...))은 관리형 트랜잭션에서 지원되지 않아 실제로
    "Query object is only supported for session.run"으로 실패함을 실측으로
    확인했다 - unit_of_work 데코레이터가 올바른 방법이다.)"""
    driver = MockAsyncNeo4jDriver(records=[])

    await execute_cypher_with_driver(driver, "RETURN 1", timeout_sec=2.5)

    assert driver.last_timeout == 2.5
    assert driver.last_transaction is not None
    assert driver.last_transaction.ran_query_text == "RETURN 1"


async def test_execute_cypher_propagates_original_exception_without_wrapping() -> None:
    """실행 중 예외가 나면 커스텀 예외로 감싸지 않고 원본 타입 그대로 전파한다."""
    driver = MockAsyncNeo4jDriver(error=AuthError("unauthorized"))

    with pytest.raises(AuthError):
        await execute_cypher_with_driver(driver, "RETURN 1", timeout_sec=5.0)
