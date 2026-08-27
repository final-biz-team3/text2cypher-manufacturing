import os

import psycopg
from fastapi import APIRouter

from core.neo4j import get_driver
from core.postgres import postgres_conninfo

router = APIRouter()


@router.get("/health")
async def health_check():
    neo4j_status = "ok"
    neo4j_detail = None

    try:
        driver = get_driver()
        await driver.verify_connectivity()
    except Exception as e:
        neo4j_status = "error"
        neo4j_detail = str(e)

    postgres_status = "ok"
    postgres_detail = None

    try:
        # 트래픽용 read pool(get_pool())을 빌리지 않는다 - 부하로 풀이 꽉 차
        # 있으면 DB 자체는 멀쩡한데도 헬스체크만 커넥션을 못 받아 타임아웃
        # 나고, liveness probe로 쓰이는 경우 멀쩡한 pod가 재시작될 수 있다.
        async with await psycopg.AsyncConnection.connect(postgres_conninfo()) as conn:
            await conn.execute("SELECT 1")
    except Exception as e:
        postgres_status = "error"
        postgres_detail = str(e)

    return {
        "status": "ok",
        "env": os.getenv("APP_ENV", "development"),
        "neo4j": {
            "status": neo4j_status,
            **({"detail": neo4j_detail} if neo4j_detail else {}),
        },
        "postgres": {
            "status": postgres_status,
            **({"detail": postgres_detail} if postgres_detail else {}),
        },
    }
