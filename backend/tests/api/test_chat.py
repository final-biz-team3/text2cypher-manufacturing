"""POST /chat 핸들러가 confirmed_entity를 오케스트레이터에 전달하고, 로그인한
사용자 이름으로 대화기록을 저장하는 동작을 테스트한다."""

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from pydantic import ValidationError

import api.chat as chat_module
from api.chat import ChatRequest, chat
from core.auth import CurrentUser, create_access_token
from tests.mocks.openai import (
    MockOpenAIClient,
    make_content_response,
    make_no_tool_call_response,
)
from tests.mocks.postgres import MockAsyncPostgresPool, MockAsyncWritePool

_READ = make_content_response(
    '{"intent":"READ","confidence":0.99,"reason":"조회 요청"}'
)


def _fake_request() -> Request:
    """chat()이 캐시된 그래프를 찾아보는 request.app.state.graph 접근을 만족시키는
    최소 Request. lifespan을 거치지 않은 맨 FastAPI() 앱을 물려서, chat()이
    캐시를 못 찾고(app.state에 graph가 없음) 기존처럼 그 자리에서 새로
    그래프를 빌드하는 경로로 자연스럽게 빠지게 한다."""
    return Request({"type": "http", "app": FastAPI(), "headers": []})


async def test_chat_passes_confirmed_entity_and_runs_sql_agent_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """confirmed_entity를 검증·유지하고 SQL 생성·실행을 한 번 시도한다."""
    openai_client = MockOpenAIClient(
        _READ,
        make_no_tool_call_response(),
        make_content_response('["sql"]'),
        make_content_response(
            "SELECT listprice FROM production.product WHERE productid = 956"
        ),
    )
    monkeypatch.setattr(chat_module, "get_openai_client", lambda: openai_client)
    monkeypatch.setattr(
        chat_module,
        "get_pool",
        lambda: MockAsyncPostgresPool(
            rows_by_name={"Touring-1000 Yellow, 54": (956, "Touring-1000 Yellow, 54")}
        ),
    )
    monkeypatch.setattr(chat_module, "get_write_pool", lambda: MockAsyncWritePool())

    result = await chat(
        ChatRequest(
            query="그 제품 정가 알려줘.",
            confirmed_entity={
                "productId": 956,
                "productName": "Touring-1000 Yellow, 54",
            },
        ),
        request=_fake_request(),
        user=CurrentUser(username="kim.quality", role="user"),
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
    assert "subqueries" not in result
    assert len(openai_client.calls) == 4
    assert result["normalization_elapsed_ms"] >= 0


async def test_chat_saves_conversation_history(monkeypatch: pytest.MonkeyPatch) -> None:
    """/chat 호출 후 로그인한 사용자 이름으로 대화기록이 저장된다."""
    openai_client = MockOpenAIClient(
        _READ,
        make_no_tool_call_response(),
        make_content_response('["sql"]'),
        make_content_response("SELECT listprice FROM production.product"),
    )
    monkeypatch.setattr(chat_module, "get_openai_client", lambda: openai_client)
    monkeypatch.setattr(
        chat_module,
        "get_pool",
        lambda: MockAsyncPostgresPool(
            rows_by_name={"Touring-1000 Yellow, 54": (956, "Touring-1000 Yellow, 54")}
        ),
    )
    write_pool = MockAsyncWritePool()
    monkeypatch.setattr(chat_module, "get_write_pool", lambda: write_pool)

    await chat(
        ChatRequest(
            query="그 제품 정가 알려줘.",
            confirmed_entity={
                "productId": 956,
                "productName": "Touring-1000 Yellow, 54",
            },
        ),
        request=_fake_request(),
        user=CurrentUser(username="kim.quality", role="user"),
    )

    assert write_pool.statements
    query, params = write_pool.statements[0]
    assert "INSERT INTO app.conversation_history" in query
    assert params[0] == "kim.quality"
    assert params[1] == "그 제품 정가 알려줘."
    assert write_pool.committed is True


class _FailingWritePool:
    """대화기록 INSERT만 실패시킨다."""

    def connection(self) -> "_FailingWriteConnectionContext":
        return _FailingWriteConnectionContext()

    def get_stats(self) -> dict[str, int]:
        return {}


class _FailingWriteConnectionContext:
    async def __aenter__(self) -> "_FailingWriteConnection":
        return _FailingWriteConnection()

    async def __aexit__(self, *exc_info: object) -> bool:
        return False


class _FailingWriteConnection:
    async def execute(self, query: str, params: tuple = ()):
        raise RuntimeError("db down")

    async def rollback(self) -> None:
        return None


async def test_chat_returns_response_even_if_save_conversation_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """대화기록 저장이 실패해도 /chat 응답 자체는 정상 반환된다."""
    openai_client = MockOpenAIClient(
        _READ,
        make_no_tool_call_response(),
        make_content_response('["sql"]'),
        make_content_response("SELECT listprice FROM production.product"),
    )
    monkeypatch.setattr(chat_module, "get_openai_client", lambda: openai_client)
    monkeypatch.setattr(
        chat_module,
        "get_pool",
        lambda: MockAsyncPostgresPool(
            rows_by_name={"Touring-1000 Yellow, 54": (956, "Touring-1000 Yellow, 54")}
        ),
    )
    monkeypatch.setattr(chat_module, "get_write_pool", lambda: _FailingWritePool())

    result = await chat(
        ChatRequest(
            query="그 제품 정가 알려줘.",
            confirmed_entity={
                "productId": 956,
                "productName": "Touring-1000 Yellow, 54",
            },
        ),
        request=_fake_request(),
        user=CurrentUser(username="kim.quality", role="user"),
    )

    assert result["sql_query"] == "SELECT listprice FROM production.product"


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
        _READ,
        make_no_tool_call_response(),
        make_content_response('["sql"]'),
        make_content_response("SELECT listprice FROM production.product"),
    )
    monkeypatch.setattr(chat_module, "get_openai_client", lambda: openai_client)
    monkeypatch.setattr(
        chat_module, "get_pool", lambda: MockAsyncPostgresPool(rows_by_name={})
    )
    monkeypatch.setattr(chat_module, "get_write_pool", lambda: MockAsyncWritePool())
    app = FastAPI()
    app.include_router(chat_module.router)
    client = TestClient(app)
    client.cookies.set("access_token", create_access_token("kim.quality", "admin"))

    response = client.post("/chat", json={"query": "정가 알려줘"})

    assert response.status_code == 200
