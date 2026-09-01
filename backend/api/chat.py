import logging
from typing import Any

import neo4j.time
from fastapi import APIRouter, Depends, Request
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, ConfigDict, Field

from core.auth import CurrentUser, get_current_user
from core.history import save_conversation
from core.openai_client import get_openai_client
from core.postgres import get_pool, get_write_pool
from orchestrator.errors import EntityNotFoundError
from orchestrator.graph import build_orchestrator_graph
from orchestrator.nodes.generate_answer import generate_failure_answer
from orchestrator.nodes.plan_outputs import OutputPlanningError
from orchestrator.nodes.route_query import RoutePlanError
from orchestrator.query_failures import (
    entity_not_found_failure,
    query_understanding_failure,
)
from orchestrator.state import QueryFailure

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


def _safe_failed_result(value: Any) -> Any:
    """실패 응답·기록에서 원본 오류, 시도 쿼리, 내부 실패 타입을 제거한다."""
    if not isinstance(value, dict):
        return value
    return {
        "result": value.get("result"),
        "error": (
            "질의를 완료하지 못했습니다." if value.get("error") is not None else None
        ),
        "attempts": [],
        "empty_reason": value.get("empty_reason"),
        "truncated": value.get("truncated", False),
    }


def _tool_failed(failure: QueryFailure | None, tool: str) -> bool:
    """실패 도구만 숨기되 도구를 특정할 수 없는 조기 실패는 모두 숨긴다."""
    if failure is None:
        return False
    failed_tool = failure.get("failed_tool")
    return failed_tool is None or failed_tool == tool


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str
    confirmed_entity: dict | list[dict] | None = None


class RetryAttempt(BaseModel):
    query: str
    error: str | None


class QueryOutcome(BaseModel):
    """execute_plan/retry_agent가 SQL·Cypher 실행마다 만드는 결과 요약
    (orchestrator/nodes/execute_plan.py의 _result_summary와 shape이 같다)."""

    result: list[dict[str, Any]] | None
    error: str | None
    attempts: list[RetryAttempt] = Field(default_factory=list)
    empty_reason: str | None = None


class ChatResponse(BaseModel):
    """POST /chat 응답 계약. 필드 목록이 이 모델 하나로 고정돼, orchestrator
    내부 전용 필드(composed_result 등)가 실수로 새어나가는 걸 막는다."""

    query: str
    entity: dict | list[dict] | None = None
    tool_plan: list[str] | None = None
    sql_query: str | None = None
    cypher_query: str | None = None
    sql_result: QueryOutcome | None = None
    graph_result: QueryOutcome | None = None
    final_answer: str | None = None


@router.post("/chat", response_model=ChatResponse)
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
    try:
        result = await graph.ainvoke(
            {
                "query": chat_request.query,
                "confirmed_entity": chat_request.confirmed_entity,
            }
        )
    except EntityNotFoundError:
        final_answer = await generate_failure_answer(
            get_openai_client(),
            query=chat_request.query,
            failure=entity_not_found_failure(),
        )
        result = {
            "query": chat_request.query,
            "final_answer": final_answer,
            "query_failure": entity_not_found_failure(),
        }
    except RoutePlanError:
        final_answer = await generate_failure_answer(
            get_openai_client(),
            query=chat_request.query,
            failure=query_understanding_failure("routing"),
        )
        result = {
            "query": chat_request.query,
            "final_answer": final_answer,
            "query_failure": query_understanding_failure("routing"),
        }
    except OutputPlanningError:
        final_answer = await generate_failure_answer(
            get_openai_client(),
            query=chat_request.query,
            failure=query_understanding_failure("planning"),
        )
        result = {
            "query": chat_request.query,
            "final_answer": final_answer,
            "query_failure": query_understanding_failure("planning"),
        }
    # entity도 이론상 조회 결과에서 온 값이라(현재는 항상 int id/str name
    # 조합이라 실제로 걸린 적은 없지만) sql_result/graph_result만 따로
    # 변환하면 나중에 다른 필드에서 같은 버그가 재현될 수 있다 - response
    # 전체를 한 번에 감싼다. 이후로는 이 ChatResponse 하나만 쓰고, dict로
    # 다시 풀어보지 않는다(응답 계약을 두 표현으로 쪼개면 필드명이
    # 어긋나도 잡아낼 방법이 없다).
    query_failure: QueryFailure | None = result.get("query_failure")
    sql_failed = _tool_failed(query_failure, "sql")
    graph_failed = _tool_failed(query_failure, "graph")
    response = ChatResponse(
        **_to_json_safe(
            {
                "query": result["query"],
                "entity": result.get("entity"),
                "tool_plan": result.get("tool_plan"),
                "sql_query": None if sql_failed else result.get("sql_query"),
                "cypher_query": (None if graph_failed else result.get("cypher_query")),
                "sql_result": (
                    _safe_failed_result(result.get("sql_result"))
                    if sql_failed
                    else result.get("sql_result")
                ),
                "graph_result": (
                    _safe_failed_result(result.get("graph_result"))
                    if graph_failed
                    else result.get("graph_result")
                ),
                "final_answer": result.get("final_answer"),
            }
        )
    )
    try:
        # 대화기록 저장은 쓰기(INSERT)라 조회 전용 get_pool()이 아니라
        # read_only가 안 걸린 별도의 write pool을 쓴다. get_write_pool()의
        # 실제 반환 타입(AsyncConnectionPool)이 core.history.Pool Protocol과
        # mypy 구조적 검사에서만 어긋나는 이유는 core/history.py의 Pool
        # 주석 참고(실측 확인된 mypy 한계).
        await save_conversation(
            get_write_pool(),  # type: ignore[arg-type]
            user.username,
            response.query,
            response.final_answer,
            response.sql_query,
            response.cypher_query,
            response.sql_result.model_dump() if response.sql_result else None,
            response.graph_result.model_dump() if response.graph_result else None,
        )
    except Exception:
        # write pool 고갈(PoolTimeout 등)로 실패했는지 구분할 수 있도록 그
        # 순간의 풀 상태를 같이 남긴다 - POSTGRES_WRITE_POOL_MAX_SIZE를
        # 실측으로 조정하려면 이 로그가 근거 데이터가 된다.
        logger.exception(
            "save_conversation 실패: username=%r query=%r pool_stats=%s",
            user.username,
            response.query,
            get_write_pool().get_stats(),
        )
    return response
