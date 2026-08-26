from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict

from core.auth import CurrentUser, get_current_user
from core.openai_client import get_openai_client
from core.postgres import get_connection
from orchestrator.graph import build_orchestrator_graph

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
    graph = build_orchestrator_graph(get_openai_client(), get_connection())
    result = graph.invoke(
        {"query": request.query, "confirmed_entity": request.confirmed_entity}
    )
    return {
        "query": result["query"],
        "entity": result.get("entity"),
        "tool_plan": result.get("tool_plan"),
        "sql_query": result.get("sql_query"),
        "cypher_query": result.get("cypher_query"),
        "sql_result": result.get("sql_result"),
        "graph_result": result.get("graph_result"),
        "final_answer": result.get("final_answer"),
    }
