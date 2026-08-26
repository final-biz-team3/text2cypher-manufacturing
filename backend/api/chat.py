import logging

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict

from core.auth import CurrentUser, get_current_user
from core.history import save_conversation
from core.openai_client import get_openai_client
from core.postgres import get_connection
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
    connection = get_connection()
    graph = build_orchestrator_graph(get_openai_client(), connection)
    result = graph.invoke(
        {"query": request.query, "confirmed_entity": request.confirmed_entity}
    )
    response = {
        "query": result["query"],
        "normalized_query": result.get("normalized_query"),
        "matched_terms": result.get("matched_terms", []),
        "normalization_status": result.get("normalization_status"),
        "ambiguous_terms": result.get("ambiguous_terms", []),
        "normalization_elapsed_ms": result.get("normalization_elapsed_ms"),
        "natural_guard": result.get("natural_guard"),
        "query_guard": result.get("query_guard"),
        "execution_allowed": result.get("execution_allowed", False),
        "error": result.get("error"),
        "entity": result.get("entity"),
        "tool_plan": result.get("tool_plan"),
        "sql_query": result.get("sql_query"),
        "cypher_query": result.get("cypher_query"),
        "sql_result": result.get("sql_result"),
        "graph_result": result.get("graph_result"),
        "final_answer": result.get("final_answer"),
    }
    try:
        save_conversation(
            connection,
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
