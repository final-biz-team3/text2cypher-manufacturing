from fastapi import APIRouter
from core.neo4j import get_driver
from core.config import settings

router = APIRouter()


@router.get("/health")
async def health_check():
    neo4j_status = "ok"
    neo4j_detail = None

    try:
        driver = get_driver()
        driver.verify_connectivity()
    except Exception as e:
        neo4j_status = "error"
        neo4j_detail = str(e)

    return {
        "status": "ok",
        "env": settings.app_env,
        "neo4j": {
            "status": neo4j_status,
            **({"detail": neo4j_detail} if neo4j_detail else {}),
        },
    }
