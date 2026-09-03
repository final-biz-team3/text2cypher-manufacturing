import logging
import sys
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response

from api.auth import router as auth_router
from api.chat import router as chat_router
from api.dashboard import router as dashboard_router
from api.entities import router as entities_router
from api.health import router as health_router
from api.history import router as history_router
from core.auth import check_jwt_secret
from core.event_loop import use_windows_selector_event_loop_policy
from core.neo4j import close_driver, get_driver
from core.openai_client import get_openai_client
from core.postgres import bootstrap_postgres, close_pool, get_pool, open_pool
from orchestrator.errors import AppError
from orchestrator.execution.cypher_executor import (
    close_reader_driver,
    get_reader_driver,
    verify_reader_is_read_only,
)
from orchestrator.graph import build_orchestrator_graph

load_dotenv()

# uvicorn이 이벤트 루프를 만들기 전, 모듈 로드 시점에 걸어야 한다.
use_windows_selector_event_loop_policy()

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
    try:
        await bootstrap_postgres()
        await open_pool()
        try:
            # execute_cypher 전용 reader 드라이버(관리자 드라이버와 별개)도
            # open_pool(wait=True)와 같은 이유로 시작 시점에 접속과 권한을
            # 확인한다 - 계정/비밀번호가 틀리거나 reader role이 아니어도
            # 시작은 성공한 것처럼 보이다가 첫 Cypher 실행 요청에서야
            # 드러나는 걸 막는다. 이 두 확인을 close_reader_driver()를 도는
            # try 블록 "안"에서 해야 한다 - 밖에서 하면 검증 자체가
            # 실패했을 때(정확히 이게 잡으려는 상황) 이미 만든 드라이버가
            # 정리 안 되고 새는 버그가 난다(리뷰에서 지적받음).
            reader_driver = get_reader_driver()
            try:
                await reader_driver.verify_connectivity()
                await verify_reader_is_read_only(reader_driver)
                # 요청마다 스키마 YAML을 다시 파싱하고 StateGraph를 재컴파일하는
                # 걸 막기 위해 시작 시 한 번만 빌드해 캐싱한다 (api/chat.py가
                # app.state.graph를 읽는다).
                app.state.graph = build_orchestrator_graph(
                    get_openai_client(), get_pool()
                )
                yield
            finally:
                await close_reader_driver()
        finally:
            # 자원은 만든 순서(Neo4j 관리자 드라이버 → Postgres 풀 → Neo4j
            # reader 드라이버)의 역순으로 정리한다. 이 finally가 없으면
            # open_pool()/reader 드라이버 접속 확인 실패 시 yield 이전에
            # 예외가 던져져 아래 close_driver()가 아예 실행되지 않고 이미
            # 만든 Neo4j 드라이버가 누수된다.
            await close_pool()
    finally:
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
app.include_router(dashboard_router, tags=["Dashboard"])
app.include_router(entities_router, tags=["Entities"])


@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError):
    # RetryExceededError는 여기 등록돼 있지만 self-correction 설계상(retry_agent.py)
    # 재시도 루프가 소진돼도 raise하지 않고 error 필드로만 전달하도록 구현돼 있어
    # 이 경로로는 절대 들어오지 않는다.
    content = {"code": exc.code, "message": exc.message}
    if hasattr(exc, "candidates"):
        content["candidates"] = exc.candidates
    if hasattr(exc, "lookup_name"):
        content["lookupName"] = exc.lookup_name
    return JSONResponse(status_code=exc.status_code, content=content)


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return Response(status_code=204)


if __name__ == "__main__":
    import uvicorn

    # Windows에서는 loop="none"이 필요하다 - 안 그러면 uvicorn이
    # asyncio.run()에 명시적으로 loop_factory=asyncio.ProactorEventLoop를
    # 넘겨, 위에서 건 이벤트 루프 정책을 무시하고 psycopg async가
    # InterfaceError로 즉시 실패한다. Linux(배포 환경)에는 이 문제가 없고,
    # 여기서 무조건 "none"을 걸면 uvicorn의 자동 uvloop 적용만 막혀버리므로
    # Windows에서만 적용한다.
    loop_kind = "none" if sys.platform == "win32" else "auto"
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True, loop=loop_kind)
