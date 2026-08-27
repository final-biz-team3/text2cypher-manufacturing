import logging
from typing import Any

import neo4j.time
from fastapi import APIRouter, Depends, Request
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, ConfigDict

from core.auth import CurrentUser, get_current_user
from core.history import save_conversation
from core.openai_client import get_openai_client
from core.postgres import get_pool, get_write_pool
from orchestrator.graph import build_orchestrator_graph

logger = logging.getLogger(__name__)

router = APIRouter()

# fastapi.encoders.jsonable_encoder는 Decimal은 알아서 float로 바꾸지만
# neo4j.time.DateTime/Date/Time/Duration은 모르는 타입이라 __dict__를
# 그대로 덤프해버린다(예: {"_DateTime__date": {...}} 같은 내부 속성명이
# 그대로 샌다 - 실측으로 확인함). ISO 문자열로 명시적으로 바꿔준다.
_NEO4J_TEMPORAL_ENCODERS: dict[type, Any] = {
    neo4j.time.DateTime: lambda v: v.iso_format(),
    neo4j.time.Date: lambda v: v.iso_format(),
    neo4j.time.Time: lambda v: v.iso_format(),
    neo4j.time.Duration: str,
}


def _to_json_safe(value: Any) -> Any:
    """sql_result/graph_result를 HTTP 응답과 save_conversation의
    json.dumps() 양쪽에 안전하게 쓸 수 있는 순수 JSON 타입으로 미리
    바꿔둔다. Decimal(SQL)과 neo4j.time.*(Cypher) 둘 다 plain json.dumps는
    아예 못 다루므로(TypeError), 대화기록 저장이 이런 값을 만날 때마다
    조용히 실패하고 있었다 - 여기서 한 번만 변환해 두 경로 모두 해결한다."""
    return jsonable_encoder(value, custom_encoder=_NEO4J_TEMPORAL_ENCODERS)


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str
    confirmed_entity: dict | None = None


@router.post("/chat")
async def chat(
    chat_request: ChatRequest,
    request: Request,
    user: CurrentUser = Depends(get_current_user),  # noqa: B008
):
    # main.py의 lifespan()이 시작 시 한 번 빌드해 app.state.graph에 캐싱해둔
    # 그래프를 재사용한다 - 요청마다 스키마 YAML을 다시 파싱하고
    # StateGraph를 재컴파일하는 건 이 경로에 남은 유일한 동기 블로킹
    # 구간이었다. app.state에 캐시가 없으면(lifespan 미구성 - 테스트 등)
    # 기존처럼 그 자리에서 새로 빌드한다.
    graph = getattr(request.app.state, "graph", None)
    if graph is None:
        graph = build_orchestrator_graph(get_openai_client(), get_pool())
    result = await graph.ainvoke(
        {
            "query": chat_request.query,
            "confirmed_entity": chat_request.confirmed_entity,
        }
    )
    response = {
        "query": result["query"],
        "entity": result.get("entity"),
        "tool_plan": result.get("tool_plan"),
        "sql_query": result.get("sql_query"),
        "cypher_query": result.get("cypher_query"),
        "sql_result": _to_json_safe(result.get("sql_result")),
        "graph_result": _to_json_safe(result.get("graph_result")),
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
        # write pool 고갈(PoolTimeout 등)로 실패했는지 구분할 수 있도록 그
        # 순간의 풀 상태를 같이 남긴다 - POSTGRES_WRITE_POOL_MAX_SIZE를
        # 실측으로 조정하려면 이 로그가 근거 데이터가 된다.
        logger.exception(
            "save_conversation 실패: username=%r query=%r pool_stats=%s",
            user.username,
            response["query"],
            get_write_pool().get_stats(),
        )
    return response
