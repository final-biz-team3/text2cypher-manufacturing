from fastapi import APIRouter
from pydantic import BaseModel

from core.openai_client import get_openai_client
from core.postgres import get_connection
from orchestrator.graph import build_orchestrator_graph

router = APIRouter()


# /chat 요청 바디: 자연어 질의 하나만 받는다
class ChatRequest(BaseModel):
    query: str


# /chat은 현재 질의 원문, 확정된 entity와 tool_plan을 반환한다.
@router.post("/chat")
async def chat(request: ChatRequest):
    graph = build_orchestrator_graph(get_openai_client(), get_connection())
    result = graph.invoke({"query": request.query})
    return {
        "query": result["query"],
        "entity": result.get("entity"),
        "tool_plan": result.get("tool_plan"),
    }
