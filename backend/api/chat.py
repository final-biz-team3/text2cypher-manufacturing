from fastapi import APIRouter
from pydantic import BaseModel

from core.openai_client import get_openai_client
from core.postgres import get_connection
from orchestrator.graph import build_orchestrator_graph

router = APIRouter()


# /chat 요청 바디: 자연어 질의 하나만 받는다
class ChatRequest(BaseModel):
    query: str


# 자연어 질의를 오케스트레이터 그래프(resolve_entity -> route_query)로 라우팅
# run_agents/generate_answer가 아직 없어 answer/sql/cypher는 반환하지 않는다
# -> 이번 범위(엔티티 확정 + 분기)의 결과만 그대로 노출하는 테스트용 엔드포인트
@router.post("/chat")
async def chat(request: ChatRequest):
    graph = build_orchestrator_graph(get_openai_client(), get_connection())
    result = graph.invoke({"query": request.query})
    return {
        "query": result["query"],
        "entity": result.get("entity"),
        "tool_plan": result.get("tool_plan"),
    }
