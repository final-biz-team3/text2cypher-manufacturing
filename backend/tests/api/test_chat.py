"""POST /chat 핸들러가 confirmed_entity를 오케스트레이터에 전달하고, 로그인한
사용자 이름으로 대화기록을 저장하는 동작을 테스트한다."""

import json
from decimal import Decimal

import neo4j.time
import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from pydantic import ValidationError

import api.chat as chat_module
import orchestrator.graph as graph_module
from api.chat import ChatRequest, chat
from core.auth import CurrentUser, create_access_token
from main import app as main_app
from orchestrator.errors import AnswerGenerationError, EntityNotFoundError
from orchestrator.nodes.plan_outputs import OutputPlanningError
from orchestrator.nodes.route_query import RoutePlanError
from orchestrator.query_failures import (
    entity_not_found_failure,
    query_understanding_failure,
)
from orchestrator.state import QueryFailure
from tests.mocks.openai import (
    MockChatCompletion,
    MockOpenAIClient,
    make_content_response,
    make_no_tool_call_response,
)
from tests.mocks.postgres import MockAsyncPostgresPool, MockAsyncWritePool

_ANSWER = "정가는 **2,384.07**입니다."


def _answering_client(*responses: MockChatCompletion) -> MockOpenAIClient:
    return MockOpenAIClient(*responses, make_content_response(_ANSWER))


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
    openai_client = _answering_client(
        make_content_response("ON_TOPIC"),
        make_no_tool_call_response(),
        make_content_response('["sql"]'),
        make_content_response('{"requiredOutputs":["listPrice"]}'),
        make_content_response(
            'SELECT listprice AS "listPrice" FROM production.product '
            "WHERE productid = 956"
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

    # execute_sql은 pool 인자로 주입되는 게 아니라 orchestrator.graph 모듈
    # 전역에서 core.postgres.get_pool()을 직접 참조하는 실제 함수라
    # (Task 5), chat_module의 get_pool monkeypatch로는 못 가로챈다 -
    # graph_module.execute_sql 자체를 가짜로 바꿔야 실제 DB를 안 친다.
    async def fake_execute_sql(sql: str) -> list[dict]:
        return [{"listPrice": 2384.07}]

    monkeypatch.setattr(graph_module, "execute_sql", fake_execute_sql)

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
        'SELECT listprice AS "listPrice" FROM production.product WHERE productid = 956'
    )
    assert result["sql_result"]["error"] is None
    assert result["sql_result"]["result"] == [{"listPrice": 2384.07}]
    assert result["final_answer"] == _ANSWER
    assert "composed_result" not in result
    assert "resultTransform" not in result
    assert "subqueries" not in result
    assert "subquery_results" not in result
    assert len(openai_client.calls) == 6


async def test_chat_saves_conversation_history(monkeypatch: pytest.MonkeyPatch) -> None:
    """/chat 호출 후 로그인한 사용자 이름으로 대화기록이 저장된다."""
    openai_client = _answering_client(
        make_no_tool_call_response(),
        make_content_response('["sql"]'),
        make_content_response('{"requiredOutputs":["listPrice"]}'),
        make_content_response(
            'SELECT listprice AS "listPrice" FROM production.product'
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
    write_pool = MockAsyncWritePool()
    monkeypatch.setattr(chat_module, "get_write_pool", lambda: write_pool)

    async def fake_execute_sql(sql: str) -> list[dict]:
        return [{"listPrice": 2384.07}]

    monkeypatch.setattr(graph_module, "execute_sql", fake_execute_sql)

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


async def test_chat_returns_response_even_if_save_conversation_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """대화기록 저장이 실패해도 /chat 응답 자체는 정상 반환된다."""
    openai_client = _answering_client(
        make_content_response("ON_TOPIC"),
        make_no_tool_call_response(),
        make_content_response('["sql"]'),
        make_content_response('{"requiredOutputs":["listPrice"]}'),
        make_content_response(
            'SELECT listprice AS "listPrice" FROM production.product'
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
    monkeypatch.setattr(chat_module, "get_write_pool", lambda: _FailingWritePool())

    async def fake_execute_sql(sql: str) -> list[dict]:
        return [{"listPrice": 2384.07}]

    monkeypatch.setattr(graph_module, "execute_sql", fake_execute_sql)

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

    assert (
        result["sql_query"] == 'SELECT listprice AS "listPrice" FROM production.product'
    )


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
    openai_client = _answering_client(
        make_no_tool_call_response(),
        make_content_response('["sql"]'),
        make_content_response('{"requiredOutputs":["listPrice"]}'),
        make_content_response(
            'SELECT listprice AS "listPrice" FROM production.product'
        ),
    )
    monkeypatch.setattr(chat_module, "get_openai_client", lambda: openai_client)
    monkeypatch.setattr(
        chat_module, "get_pool", lambda: MockAsyncPostgresPool(rows_by_name={})
    )
    monkeypatch.setattr(chat_module, "get_write_pool", lambda: MockAsyncWritePool())

    async def fake_execute_sql(sql: str) -> list[dict]:
        return [{"listPrice": 2384.07}]

    monkeypatch.setattr(graph_module, "execute_sql", fake_execute_sql)
    app = FastAPI()
    app.include_router(chat_module.router)
    client = TestClient(app)
    client.cookies.set("access_token", create_access_token("kim.quality", "admin"))

    response = client.post("/chat", json={"query": "정가 알려줘"})

    assert response.status_code == 200


class _AnswerFailingGraph:
    async def ainvoke(self, state: dict) -> dict:
        raise AnswerGenerationError()


class _RaisingGraph:
    def __init__(self, error: Exception) -> None:
        self.error = error

    async def ainvoke(self, state: dict) -> dict:
        raise self.error


class _FailedResultGraph:
    async def ainvoke(self, state: dict) -> dict:
        return {
            "query": state["query"],
            "sql_query": "SELECT * FROM secret_table",
            "sql_result": {
                "result": None,
                "error": "database secret error",
                "attempts": [
                    {
                        "query": "SELECT * FROM secret_table",
                        "error": "database secret error",
                    }
                ],
                "failure": {"internal": "secret"},
            },
            "graph_result": None,
            "query_failure": {
                "code": "QUERY_EXECUTION_FAILED",
                "stage": "execution",
                "category": "QUERY_INVALID",
                "kind": "user_correctable",
                "retryable": True,
                "user_safe_reason": "조회에 실패했습니다.",
                "suggested_action": "조건을 구체화해 주세요.",
                "failed_tool": "sql",
                "dependent_failure": False,
            },
            "final_answer": "조건을 구체화해 다시 질문해 주세요.",
        }


class _PartiallyFailedResultGraph:
    async def ainvoke(self, state: dict) -> dict:
        return {
            "query": state["query"],
            "sql_query": "SELECT product_id FROM production.product",
            "cypher_query": "MATCH (secret) RETURN secret",
            "sql_result": {
                "result": [{"product_id": 1}],
                "error": None,
                "attempts": [
                    {
                        "query": "SELECT product_id FROM production.product",
                        "error": None,
                    }
                ],
            },
            "graph_result": {
                "result": None,
                "error": "secret graph error",
                "attempts": [
                    {"query": "MATCH (secret) RETURN secret", "error": "secret"}
                ],
            },
            "query_failure": {
                "code": "QUERY_EXECUTION_FAILED",
                "stage": "execution",
                "category": "QUERY_INVALID",
                "kind": "user_correctable",
                "retryable": True,
                "user_safe_reason": "조회에 실패했습니다.",
                "suggested_action": "조건을 구체화해 주세요.",
                "failed_tool": "graph",
                "dependent_failure": False,
            },
            "final_answer": "그래프 조회 조건을 확인해 주세요.",
        }


def test_chat_endpoint_returns_502_and_does_not_save_on_answer_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret-at-least-32-characters-long")
    monkeypatch.setattr(main_app.state, "graph", _AnswerFailingGraph(), raising=False)
    write_pool = MockAsyncWritePool()
    monkeypatch.setattr(chat_module, "get_write_pool", lambda: write_pool)
    client = TestClient(main_app, raise_server_exceptions=False)
    client.cookies.set("access_token", create_access_token("kim.quality", "admin"))

    response = client.post("/chat", json={"query": "정가 알려줘"})

    assert response.status_code == 502
    assert response.json() == {
        "code": "ANSWER_GENERATION_FAILED",
        "message": "자연어 답변을 생성하지 못했습니다. 잠시 후 다시 시도해 주세요.",
    }
    assert write_pool.statements == []


@pytest.mark.parametrize(
    ("error", "expected_failure"),
    [
        (EntityNotFoundError(), entity_not_found_failure()),
        (
            RoutePlanError("internal route error", "SECRET ROUTE RESPONSE"),
            query_understanding_failure("routing"),
        ),
        (
            OutputPlanningError("internal plan error", "SECRET PLAN RESPONSE"),
            query_understanding_failure("planning"),
        ),
    ],
)
async def test_chat_naturalizes_and_saves_user_correctable_early_failure(
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
    expected_failure: QueryFailure,
) -> None:
    """LLM 호출 없이 안전 정보를 그대로 이어붙인 답을 저장·반환한다."""
    answer = (
        f"{expected_failure['user_safe_reason']} {expected_failure['suggested_action']}"
    )
    write_pool = MockAsyncWritePool()
    monkeypatch.setattr(chat_module, "get_write_pool", lambda: write_pool)
    app = FastAPI()
    app.state.graph = _RaisingGraph(error)

    result = await chat(
        ChatRequest(query="지난달 결과를 알려줘"),
        request=Request({"type": "http", "app": app, "headers": []}),
        user=CurrentUser(username="kim.quality", role="user"),
    )

    assert result["final_answer"] == answer
    assert result["sql_query"] is None
    assert result["cypher_query"] is None
    assert write_pool.committed is True
    assert write_pool.statements[0][1][2] == answer
    assert "SECRET ROUTE RESPONSE" not in answer
    assert "SECRET PLAN RESPONSE" not in answer
    assert "internal route error" not in answer
    assert "internal plan error" not in answer


async def test_chat_removes_raw_query_and_error_from_failed_response_and_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    write_pool = MockAsyncWritePool()
    monkeypatch.setattr(chat_module, "get_write_pool", lambda: write_pool)
    app = FastAPI()
    app.state.graph = _FailedResultGraph()

    result = await chat(
        ChatRequest(query="실패하는 질의"),
        request=Request({"type": "http", "app": app, "headers": []}),
        user=CurrentUser(username="kim.quality", role="user"),
    )

    assert result["sql_query"] is None
    assert result["sql_result"]["error"] == "질의를 완료하지 못했습니다."
    assert result["sql_result"]["attempts"] == []
    serialized_response = json.dumps(result, ensure_ascii=False)
    serialized_history = json.dumps(write_pool.statements, ensure_ascii=False)
    assert "secret_table" not in serialized_response
    assert "database secret error" not in serialized_response
    assert "QUERY_EXECUTION_FAILED" not in serialized_response
    assert "secret_table" not in serialized_history
    assert "database secret error" not in serialized_history


async def test_chat_preserves_successful_tool_attempts_on_partial_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    write_pool = MockAsyncWritePool()
    monkeypatch.setattr(chat_module, "get_write_pool", lambda: write_pool)
    app = FastAPI()
    app.state.graph = _PartiallyFailedResultGraph()

    result = await chat(
        ChatRequest(query="제품과 공정 조회"),
        request=Request({"type": "http", "app": app, "headers": []}),
        user=CurrentUser(username="kim.quality", role="user"),
    )

    assert result["sql_query"] == "SELECT product_id FROM production.product"
    assert result["sql_result"]["attempts"][0]["error"] is None
    assert result["cypher_query"] is None
    assert result["graph_result"]["attempts"] == []
    assert "secret" not in json.dumps(result["graph_result"], ensure_ascii=False)


async def test_chat_serializes_decimal_and_neo4j_datetime_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """execute_sql이 반환하는 Decimal, execute_cypher가 반환하는
    neo4j.time.DateTime 둘 다 HTTP 응답에 JSON 안전한 값으로 담긴다.
    (jsonable_encoder는 Decimal은 알아서 처리하지만 neo4j.time.DateTime은
    __dict__를 그대로 덤프해버려 명시적 변환이 필요했다 - 실측으로 확인함.)"""
    openai_client = _answering_client(
        make_content_response("ON_TOPIC"),
        make_no_tool_call_response(),
        make_content_response('["sql", "graph"]'),
        make_content_response('{"requiredOutputs":["listPrice"]}'),
        make_content_response('{"requiredOutputs":["productId"]}'),
        make_content_response(
            'SELECT listprice AS "listPrice" FROM production.product'
        ),
        make_content_response(
            "MATCH (p:Product) RETURN p.productId AS productId, "
            "p.name AS productName LIMIT 1"
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
    write_pool = MockAsyncWritePool()
    monkeypatch.setattr(chat_module, "get_write_pool", lambda: write_pool)

    async def fake_execute_sql(sql: str) -> list[dict]:
        return [{"listPrice": Decimal("2384.07")}]

    async def fake_execute_cypher(cypher: str) -> list[dict]:
        return [
            {
                "productId": 956,
                "productName": "Touring-1000 Yellow, 54",
                "sourceModifiedAt": neo4j.time.DateTime(
                    2014, 2, 8, 10, 1, 36, 827000000
                ),
            }
        ]

    monkeypatch.setattr(graph_module, "execute_sql", fake_execute_sql)
    monkeypatch.setattr(graph_module, "execute_cypher", fake_execute_cypher)

    result = await chat(
        ChatRequest(
            query="정가와 등록일 알려줘.",
            confirmed_entity={
                "productId": 956,
                "productName": "Touring-1000 Yellow, 54",
            },
        ),
        request=_fake_request(),
        user=CurrentUser(username="kim.quality", role="user"),
    )

    assert result["sql_result"]["result"] == [{"listPrice": 2384.07}]
    assert result["graph_result"]["result"][0]["sourceModifiedAt"] == (
        "2014-02-08T10:01:36.827000000"
    )
    # json.dumps가 그대로 통과해야 한다 - Decimal/neo4j.time.DateTime을
    # 변환 없이 넘기면 여기서 TypeError가 난다(save_conversation 내부에서도
    # 동일하게 json.dumps를 쓰므로, 응답과 저장 양쪽에 이 값이 안전해야 함).
    json.dumps(result["sql_result"])
    json.dumps(result["graph_result"])
    assert write_pool.statements


async def test_chat_keeps_source_results_but_hides_internal_composition_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """범위 밖 HYBRID 결과는 final_answer에서 차단하되 API 필드는 유지한다."""
    route_plan = """{
      "tool_plan": ["sql", "graph"],
      "subqueries": [
        {
          "id": "sql_base",
          "tool": "sql",
          "question": "기준 사실을 조회한다.",
          "dependsOn": [],
          "joinKeys": ["productId"],
          "inputBindings": {}
        },
        {
          "id": "graph_followup",
          "tool": "graph",
          "question": "관련 경로를 조회한다.",
          "dependsOn": [],
          "joinKeys": ["productId"],
          "inputBindings": {}
        }
      ],
      "resultTransform": null
    }"""
    openai_client = MockOpenAIClient(
        make_content_response("ON_TOPIC"),
        make_no_tool_call_response(),
        make_content_response(route_plan),
        make_content_response('{"requiredOutputs":["productId"]}'),
        make_content_response('{"requiredOutputs":["productId"]}'),
        make_content_response("SELECT 1 AS productId, 'SQL Product' AS productName"),
        make_content_response(
            "MATCH (p:Product) RETURN p.productId AS productId, "
            "p.name AS productName LIMIT 1"
        ),
    )
    monkeypatch.setattr(chat_module, "get_openai_client", lambda: openai_client)
    monkeypatch.setattr(
        chat_module, "get_pool", lambda: MockAsyncPostgresPool(rows_by_name={})
    )
    monkeypatch.setattr(chat_module, "get_write_pool", lambda: MockAsyncWritePool())

    async def fake_execute_sql(sql: str) -> list[dict]:
        return [{"productId": 1, "productName": "SQL Product", "sqlFact": "kept"}]

    async def fake_execute_cypher(cypher: str) -> list[dict]:
        return [
            {
                "productId": 999,
                "productName": "Graph Product",
                "graphFact": "kept",
            }
        ]

    monkeypatch.setattr(graph_module, "execute_sql", fake_execute_sql)
    monkeypatch.setattr(graph_module, "execute_cypher", fake_execute_cypher)

    result = await chat(
        ChatRequest(query="독립 결과를 결합해줘."),
        request=_fake_request(),
        user=CurrentUser(username="kim.quality", role="user"),
    )

    assert result["sql_result"]["result"] == [
        {"productId": 1, "productName": "SQL Product", "sqlFact": "kept"}
    ]
    assert result["graph_result"]["result"] == [
        {"productId": 999, "productName": "Graph Product", "graphFact": "kept"}
    ]
    assert "다시 시도" in result["final_answer"]
    assert "바인딩 범위를 벗어났습니다" not in result["final_answer"]
    assert "composed_result" not in result
    assert "resultTransform" not in result
    assert "subqueries" not in result
    assert "subquery_results" not in result
