import os

from fastapi import APIRouter

from core.neo4j import get_driver
from core.postgres import get_pool

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
        async with get_pool().connection() as conn:
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
