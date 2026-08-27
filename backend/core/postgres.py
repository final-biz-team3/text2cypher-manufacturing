import logging
import os

import psycopg
from psycopg_pool import AsyncConnectionPool

logger = logging.getLogger(__name__)

_pool: AsyncConnectionPool | None = None
_write_pool: AsyncConnectionPool | None = None


def postgres_conninfo() -> str:
    """풀을 거치지 않는 일회성 커넥션(헬스체크 등)에도 재사용할 수 있도록
    공개 함수로 둔다."""
    return psycopg.conninfo.make_conninfo(
        host=os.getenv("POSTGRES_HOST", "localhost"),
        port=os.getenv("POSTGRES_PORT", "5432"),
        dbname=os.getenv("POSTGRES_DB", "postgres"),
        user=os.getenv("POSTGRES_USER", "postgres"),
        password=os.getenv("POSTGRES_PASSWORD", "changeme_local"),
    )


async def bootstrap_postgres() -> None:
    """앱 시작 시 1회: pg_trgm 확장을 풀과 무관한 임시 커넥션으로 준비한다.
    read_only가 아닌 일반 커넥션이라 CREATE EXTENSION(쓰기)이 가능하다."""
    async with await psycopg.AsyncConnection.connect(postgres_conninfo()) as conn:
        try:
            await conn.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
            await conn.commit()
        except Exception:
            await conn.rollback()
            logger.warning(
                "pg_trgm 확장을 준비하지 못했습니다 — 유사 이름 검색이 비활성화됩니다"
            )


async def configure_connection(conn: psycopg.AsyncConnection) -> None:
    """풀이 새 커넥션을 만들 때마다 read-only + statement_timeout을 건다.
    set_read_only()/execute() 둘 다 (autocommit=False 기본값에서) 암묵적으로
    트랜잭션을 여는데, psycopg_pool은 configure 콜백이 커넥션을 트랜잭션이
    열린 채로 반환하면 그 커넥션을 폐기한다 — 반드시 commit으로 닫아야 한다."""
    await conn.set_read_only(True)
    # SET은 psycopg 파라미터 바인딩(%s)을 지원하지 않아 문자열로 조립해야
    # 한다 - 대신 정수로 먼저 파싱해 SQL 구문에 그대로 새는 걸 막는다.
    timeout_ms = int(os.getenv("SQL_STATEMENT_TIMEOUT_MS", "5000"))
    await conn.execute(f"SET statement_timeout = '{timeout_ms}ms'")
    await conn.commit()


def get_pool() -> AsyncConnectionPool:
    """조회 전용 풀. 모든 커넥션이 read_only라 LLM이 생성한 쿼리를 포함해
    쓰기가 필요 없는 모든 경로(resolve_entity, 앞으로의 execute_sql 등)가
    공유한다."""
    global _pool
    if _pool is None:
        _pool = AsyncConnectionPool(
            postgres_conninfo(),
            configure=configure_connection,
            open=False,
            min_size=int(os.getenv("POSTGRES_POOL_MIN_SIZE", "1")),
            max_size=int(os.getenv("POSTGRES_POOL_MAX_SIZE", "5")),
        )
    return _pool


async def configure_write_connection(conn: psycopg.AsyncConnection) -> None:
    """read_only는 걸지 않는다 — 이 풀은 앱 코드가 직접 짠 신뢰된 쓰기
    쿼리(대화기록 저장 등) 전용이다. statement_timeout만 동일하게 건다."""
    timeout_ms = int(os.getenv("SQL_STATEMENT_TIMEOUT_MS", "5000"))
    await conn.execute(f"SET statement_timeout = '{timeout_ms}ms'")
    await conn.commit()


def get_write_pool() -> AsyncConnectionPool:
    """쓰기 전용 풀. read_only 조회 풀(get_pool())과 물리적으로 분리해,
    LLM 실행 경로의 read-only 보장을 절대 건드리지 않으면서 앱이 직접
    작성한 신뢰된 쓰기 쿼리(예: 대화기록 저장)만 여기로 흘려보낸다.
    쓰기 트래픽이 낮아 풀 크기를 조회 풀보다 작게 둔다."""
    global _write_pool
    if _write_pool is None:
        _write_pool = AsyncConnectionPool(
            postgres_conninfo(),
            configure=configure_write_connection,
            open=False,
            min_size=int(os.getenv("POSTGRES_WRITE_POOL_MIN_SIZE", "1")),
            max_size=int(os.getenv("POSTGRES_WRITE_POOL_MAX_SIZE", "2")),
        )
    return _write_pool


async def open_pool() -> None:
    """wait=True로 풀이 min_size만큼 실제로 채워질 때까지 기다린다 - 안 그러면
    open()이 바로 반환해 configure_connection() 실패나 DB 접속 불가 같은
    문제가 있어도 시작은 성공한 것처럼 보이고, 첫 요청이 PoolTimeout까지
    대기한 뒤에야 문제가 드러난다. 여기서 기다리면 시작 시점에 바로
    실패해서(lifespan에서 예외가 위로 전파됨) 원인 파악이 쉬워진다."""
    await get_pool().open(wait=True)
    await get_write_pool().open(wait=True)


async def close_pool() -> None:
    global _pool, _write_pool
    if _pool is not None:
        await _pool.close()
        _pool = None
    if _write_pool is not None:
        await _write_pool.close()
        _write_pool = None
