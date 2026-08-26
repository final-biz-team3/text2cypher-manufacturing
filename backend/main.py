import logging
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response

from api.auth import router as auth_router
from api.chat import router as chat_router
from api.health import router as health_router
from api.history import router as history_router
from core.auth import check_jwt_secret
from core.neo4j import close_driver, get_driver
from core.postgres import close_connection, get_connection
from orchestrator.errors import AppError

load_dotenv()

# orchestrator 노드의 logger.info()가 콘솔에 보이도록 루트 로거 레벨을 INFO로 설정
# (기본값 WARNING이면 resolve_entity/route_query의 라우팅 로그가 출력되지 않는다)
logging.basicConfig(
    level=logging.INFO, format="%(levelname)s:     %(name)s: %(message)s"
)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    check_jwt_secret()
    get_driver()
    get_connection()
    yield
    close_driver()
    close_connection()


app = FastAPI(
    title="ITDA — Text-to-Cypher API",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health_router, tags=["System"])
app.include_router(auth_router, tags=["Auth"])
app.include_router(chat_router, tags=["Chat"])
app.include_router(history_router, tags=["History"])


@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError):
    content = {"code": exc.code, "message": exc.message}
    if hasattr(exc, "candidates"):
        content["candidates"] = exc.candidates
    return JSONResponse(status_code=exc.status_code, content=content)


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return Response(status_code=204)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
