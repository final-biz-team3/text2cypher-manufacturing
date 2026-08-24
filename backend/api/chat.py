from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict

from core.openai_client import get_openai_client
from core.postgres import get_connection
from orchestrator.graph import build_orchestrator_graph

router = APIRouter()


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str
    confirmed_entity: dict | None = None


# /chat은 원문·정규화·안전성 판정과 기존 라우팅 결과를 반환한다.
@router.post("/chat")
async def chat(request: ChatRequest):
    graph = build_orchestrator_graph(get_openai_client(), get_connection())
    result = graph.invoke(
        {"query": request.query, "confirmed_entity": request.confirmed_entity}
    )
    return {
        "query": result["query"],
        "normalized_query": result.get("normalized_query"),
        "matched_terms": result.get("matched_terms", []),
        "natural_guard": result.get("natural_guard"),
        "entity": result.get("entity"),
        "tool_plan": result.get("tool_plan"),
        "query_guard": result.get("query_guard"),
        "execution_allowed": result.get("execution_allowed", False),
        "error": result.get("error"),
    }
