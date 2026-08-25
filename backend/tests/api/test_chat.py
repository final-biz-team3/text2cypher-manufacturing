"""POST /chat 핸들러가 confirmed_entity를 오케스트레이터에 전달하는 동작을 테스트한다."""

import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

import api.chat as chat_module
from api.chat import ChatRequest, chat
from core.auth import create_access_token
from tests.mocks.openai import (
    MockOpenAIClient,
    make_content_response,
    make_no_tool_call_response,
)
from tests.mocks.postgres import MockPostgresConnection


def test_chat_passes_confirmed_entity_and_runs_sql_agent_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """confirmed_entity가 있으면 매칭 없이 바로 라우팅으로 넘어가고, sql_agent가
    한 번 생성·실행을 시도한 뒤 200으로 정상 종료한다."""
    openai_client = MockOpenAIClient(
        make_content_response('["sql"]'),
        make_content_response(
            "SELECT listprice FROM production.product WHERE productid = 956"
        ),
    )
    monkeypatch.setattr(chat_module, "get_openai_client", lambda: openai_client)
    monkeypatch.setattr(
        chat_module,
        "get_connection",
        lambda: MockPostgresConnection(
            rows_by_name={"Touring-1000 Yellow, 54": (956, "Touring-1000 Yellow, 54")}
        ),
    )

    result = asyncio.run(
        chat(
            ChatRequest(
                query="그 제품 정가 알려줘.",
                confirmed_entity={
                    "productId": 956,
                    "productName": "Touring-1000 Yellow, 54",
                },
            )
        )
    )

    assert result["entity"] == {
        "productId": 956,
        "productName": "Touring-1000 Yellow, 54",
    }
    assert result["sql_query"] == (
        "SELECT listprice FROM production.product WHERE productid = 956"
    )
    assert "self-correction 구현에서 채운다" in result["final_answer"]
    assert "self-correction 구현에서 채운다" in result["sql_result"]["error"]
    assert len(openai_client.calls) == 2


def test_chat_request_rejects_unknown_field() -> None:
    """confirmedEntity처럼 오타난 필드는 조용히 무시되지 않고 검증 에러가 난다."""
    with pytest.raises(ValidationError):
        ChatRequest.model_validate(
            {
                "query": "그 제품 정가 알려줘.",
                "confirmedEntity": {"productId": 956},
            }
        )


def test_chat_endpoint_rejects_request_without_cookie() -> None:
    """라우터 레벨에서 인증 없이 /chat을 호출하면 401을 받는다."""
    app = FastAPI()
    app.include_router(chat_module.router)
    client = TestClient(app)

    response = client.post("/chat", json={"query": "정가 알려줘"})

    assert response.status_code == 401


def test_chat_endpoint_accepts_request_with_valid_cookie(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """유효한 access_token 쿠키가 있으면 /chat이 정상 응답한다."""
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret-at-least-32-characters-long")
    openai_client = MockOpenAIClient(
        make_no_tool_call_response(),
        make_content_response('["sql"]'),
        make_content_response("SELECT listprice FROM production.product"),
    )
    monkeypatch.setattr(chat_module, "get_openai_client", lambda: openai_client)
    monkeypatch.setattr(
        chat_module, "get_connection", lambda: MockPostgresConnection(rows_by_name={})
    )
    app = FastAPI()
    app.include_router(chat_module.router)
    client = TestClient(app)
    client.cookies.set("access_token", create_access_token("kim.quality", "admin"))

    response = client.post("/chat", json={"query": "정가 알려줘"})

    assert response.status_code == 200
