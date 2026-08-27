"""driver.session()...await session.execute_read(tx_function) 형태를 흉내내는
Neo4j async mock. 미리 지정한 레코드를 반환하거나 지정 예외를 raise한다."""

from collections.abc import Callable
from typing import Any


class _MockRecord:
    """neo4j.Record 흉내 - result.fetch(n)이 돌려주는 개별 레코드는 .data()로
    dict를 뽑는다(result.data()처럼 이미 dict인 상태가 아니다)."""

    def __init__(self, data: dict[str, Any]) -> None:
        self._data = data

    def data(self) -> dict[str, Any]:
        return self._data


class _MockAsyncResult:
    def __init__(self, records: list[dict[str, Any]]) -> None:
        self._records = records

    async def data(self) -> list[dict[str, Any]]:
        return self._records

    async def fetch(self, n: int) -> list[_MockRecord]:
        return [_MockRecord(record) for record in self._records[:n]]


class _MockAsyncTransaction:
    """tx.run(cypher)는 실제 API처럼 평문 문자열만 받는다(neo4j.Query 객체는
    관리형 트랜잭션 안에서 지원되지 않음 - 실제 드라이버로 확인한 사실)."""

    def __init__(self, records: list[dict[str, Any]], error: Exception | None) -> None:
        self._records = records
        self._error = error
        self.ran_query_text: str | None = None

    async def run(self, query: str) -> _MockAsyncResult:
        self.ran_query_text = query
        if self._error is not None:
            raise self._error
        return _MockAsyncResult(self._records)


class _MockAsyncSession:
    def __init__(self, driver: "MockAsyncNeo4jDriver") -> None:
        self._driver = driver

    async def __aenter__(self) -> "_MockAsyncSession":
        return self

    async def __aexit__(self, *exc_info: object) -> bool:
        return False

    async def execute_read(self, tx_function: Callable[[Any], Any]) -> Any:
        """실제 session.execute_read()가 neo4j.unit_of_work(timeout=...)로
        데코레이트된 tx_function의 .timeout 속성을 읽어 트랜잭션에 적용하는
        것과 동일하게, 여기서도 그 속성을 읽어 기록해둔다."""
        tx = _MockAsyncTransaction(self._driver.records, self._driver.error)
        self._driver.last_timeout = getattr(tx_function, "timeout", None)
        result = await tx_function(tx)
        self._driver.last_transaction = tx
        return result


class MockAsyncNeo4jDriver:
    """driver.session()... await session.execute_read(...)를 흉내내는 mock 드라이버.
    records가 지정되면 결과로, error가 지정되면 tx.run()에서 그 예외를 raise한다."""

    def __init__(
        self,
        records: list[dict[str, Any]] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.records = records or []
        self.error = error
        self.last_transaction: _MockAsyncTransaction | None = None
        self.last_timeout: float | None = None

    def session(self) -> _MockAsyncSession:
        return _MockAsyncSession(self)
