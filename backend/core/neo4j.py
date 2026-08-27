import os

from neo4j import AsyncDriver, AsyncGraphDatabase

_driver: AsyncDriver | None = None


def build_driver(user: str, password: str) -> AsyncDriver:
    """관리자 드라이버(이 모듈)와 orchestrator/execution/cypher_executor.py의
    reader 드라이버가 공유하는 생성 로직. NEO4J_URI는 둘 다 필수로 요구한다 -
    예전에는 이 모듈만 localhost로 조용히 fallback했는데, 그러면 실제
    배포에서 URI를 빠뜨려도 시작은 성공한 것처럼 보이다가 엉뚱한 곳에
    붙게 된다("시작 시 바로 실패" 원칙과 불일치했던 부분을 통일함)."""
    return AsyncGraphDatabase.driver(os.environ["NEO4J_URI"], auth=(user, password))


def get_driver() -> AsyncDriver:
    global _driver
    if _driver is None:
        _driver = build_driver(
            os.getenv("NEO4J_USER", "neo4j"),
            os.getenv("NEO4J_PASSWORD", "changeme_local"),
        )
    return _driver


async def close_driver() -> None:
    global _driver
    if _driver is not None:
        await _driver.close()
        _driver = None
