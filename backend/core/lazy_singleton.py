"""여러 모듈이 각자 손으로 구현하던 "없으면 만들고 있으면 재사용 + 비동기
close" 패턴을 하나로 모은다. Postgres 풀(get_pool/get_write_pool)과 Neo4j
드라이버(관리자/reader 계정) 초기화가 전부 이 모양이었다 - 읽기 계정처럼
안전과 직결된 자리일수록 패턴이 한 곳에만 있는 게, 다음에 새 계정/풀이
추가될 때도 검증된 패턴을 그대로 따르게 만든다."""

from collections.abc import Awaitable, Callable


class LazySingleton[T]:
    """factory()로 처음 쓰일 때만 생성하고, close()로 정리하면 다음 get()에서
    다시 생성한다. 생성 자체(드라이버/풀 구성)는 동기라 get()도 동기로 두고,
    정리(드라이버/풀 종료)는 비동기 메서드라 close()만 코루틴이다."""

    def __init__(
        self, factory: Callable[[], T], closer: Callable[[T], Awaitable[None]]
    ) -> None:
        self._factory = factory
        self._closer = closer
        self._instance: T | None = None

    def get(self) -> T:
        if self._instance is None:
            self._instance = self._factory()
        return self._instance

    async def close(self) -> None:
        if self._instance is not None:
            await self._closer(self._instance)
            self._instance = None
