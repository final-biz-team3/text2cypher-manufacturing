from fastapi import APIRouter
from pydantic import BaseModel

from core.openai_client import get_openai_client
from core.postgres import get_connection
from orchestrator.graph import build_orchestrator_graph

router = APIRouter()


class ChatRequest(BaseModel):
    query: str
    confirmed_entity: dict | None = None


@router.post("/chat")
async def chat(request: ChatRequest):
    graph = build_orchestrator_graph(get_openai_client(), get_connection())
    result = graph.invoke(
        {"query": request.query, "confirmed_entity": request.confirmed_entity}
    )
    return {
        "query": result["query"],
        "entity": result.get("entity"),
        "tool_plan": result.get("tool_plan"),
    }
