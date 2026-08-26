import asyncio
import logging
import sys
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
from core.postgres import bootstrap_postgres, close_pool, open_pool
from orchestrator.errors import AppError

load_dotenv()

# psycopg의 async 모드는 Windows 기본 ProactorEventLoop를 지원하지 않는다
# (InterfaceError로 즉시 거부) — WindowsSelectorEventLoopPolicy로 고정한다.
# uvicorn이 이벤트 루프를 만들기 전, 모듈 로드 시점에 걸어야 한다.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

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
    await bootstrap_postgres()
    await open_pool()
    yield
    await close_pool()
    await close_driver()


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
    # RetryExceededError는 여기 등록돼 있지만 self-correction 설계상(retry_agent.py)
    # 재시도 루프가 소진돼도 raise하지 않고 error 필드로만 전달하도록 구현돼 있어
    # 이 경로로는 절대 들어오지 않는다.
    content = {"code": exc.code, "message": exc.message}
    if hasattr(exc, "candidates"):
        content["candidates"] = exc.candidates
    return JSONResponse(status_code=exc.status_code, content=content)


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return Response(status_code=204)


if __name__ == "__main__":
    import uvicorn

    # loop="none"이 아니면 uvicorn이 Windows에서 asyncio.run()에 명시적으로
    # loop_factory=asyncio.ProactorEventLoop를 넘겨, 위에서 건 이벤트 루프
    # 정책을 무시하고 psycopg async가 InterfaceError로 즉시 실패한다.
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True, loop="none")
