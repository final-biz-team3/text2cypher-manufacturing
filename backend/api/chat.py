import logging

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict

from core.auth import CurrentUser, get_current_user
from core.history import save_conversation
from core.openai_client import get_openai_client
from core.postgres import get_pool, get_write_pool
from orchestrator.graph import build_orchestrator_graph

logger = logging.getLogger(__name__)

router = APIRouter()


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str
    confirmed_entity: dict | None = None


@router.post("/chat")
async def chat(
    request: ChatRequest,
    user: CurrentUser = Depends(get_current_user),  # noqa: B008
):
    graph = build_orchestrator_graph(get_openai_client(), get_pool())
    result = await graph.ainvoke(
        {"query": request.query, "confirmed_entity": request.confirmed_entity}
    )
    response = {
        "query": result["query"],
        "entity": result.get("entity"),
        "tool_plan": result.get("tool_plan"),
        "sql_query": result.get("sql_query"),
        "cypher_query": result.get("cypher_query"),
        "sql_result": result.get("sql_result"),
        "graph_result": result.get("graph_result"),
        "final_answer": result.get("final_answer"),
    }
    try:
        # 대화기록 저장은 쓰기(INSERT)라 조회 전용 get_pool()이 아니라
        # read_only가 안 걸린 별도의 write pool을 쓴다.
        await save_conversation(
            get_write_pool(),
            user.username,
            response["query"],
            response["final_answer"],
            response["sql_query"],
            response["cypher_query"],
            response["sql_result"],
            response["graph_result"],
        )
    except Exception:
        logger.exception(
            "save_conversation 실패: username=%r query=%r",
            user.username,
            response["query"],
        )
    return response
