"""성공한 재시도의 내부 DB 오류가 HTTP 응답과 대화 기록에 남지 않아야 한다."""

import json
import logging

import psycopg
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from neo4j.exceptions import CypherSyntaxError

import api.chat as chat_module
from core.auth import CurrentUser
from orchestrator.nodes.execute_plan import make_execute_plan_node
from orchestrator.subgraphs.retry_agent import make_retry_agent_subgraph
from tests.mocks.postgres import MockAsyncWritePool


@pytest.mark.parametrize(
    "tool,error_class",
    [("sql", psycopg.errors.InvalidColumnReference), ("graph", CypherSyntaxError)],
)
def test_recovered_db_error_is_private_in_response_and_history(
    monkeypatch: pytest.MonkeyPatch, tool: str, error_class: type[Exception]
) -> None:
    marker = "INTERNAL_DB_ERROR_CANARY"
    exact_error = f"{marker}: internal_table.secret_column, literal='private-value'"
    generated: list[str | None] = []
    executed: list[str] = []

    async def generate(state, previous_query, previous_error):
        generated.append(previous_error)
        return "SELECT 1 AS value" if tool == "sql" else "RETURN 1 AS value"

    async def execute(query):
        executed.append(query)
        if len(executed) == 1:
            raise error_class(exact_error)
        return [{"value": 1}]

    agent = make_retry_agent_subgraph(
        logger=logging.getLogger(__name__),
        label="sql_agent" if tool == "sql" else "cypher_agent",
        generate=generate,
        execute=execute,
        connection_exceptions=(),
        retryable_exceptions=(error_class,),
        empty_result_feedback="EMPTY",
    )
    node = make_execute_plan_node(
        sql_agent=agent, cypher_agent=agent, sql_schema_text="", cypher_schema_text=""
    )

    class Graph:
        async def ainvoke(self, state):
            result = await node(
                {
                    **state,
                    "query": state["query"],
                    "subqueries": [
                        {
                            "id": "one",
                            "tool": tool,
                            "question": "제품 조회",
                            "dependsOn": [],
                            "requiredOutputs": ["value"],
                            "joinKeys": [],
                        }
                    ],
                }
            )
            assert result["query_failure"] is None
            assert "retry_feedback" not in result[f"{tool}_result"]
            return {
                **result,
                "query": state["query"],
                "tool_plan": [tool],
                "final_answer": "조회 완료",
            }

    app = FastAPI()
    app.state.graph = Graph()
    app.include_router(chat_module.router)
    app.dependency_overrides[chat_module.get_current_user] = lambda: CurrentUser(
        username="retry-test", role="user"
    )
    pool = MockAsyncWritePool()
    monkeypatch.setattr(chat_module, "get_write_pool", lambda: pool)
    with TestClient(app) as client:
        response = client.post("/chat", json={"query": "제품 조회"})

    assert response.status_code == 200
    assert len(executed) == 2
    assert generated == [None, exact_error]
    outcome = response.json()[f"{tool}_result"]
    assert outcome["error"] is None
    assert [item["error"] for item in outcome["attempts"]] == [
        "쿼리를 실행하지 못했습니다.",
        None,
    ]
    assert pool.statements  # 저장 경로도 실제로 통과했는지 확인한다.
    for serialized in (response.text, json.dumps(pool.statements, ensure_ascii=False)):
        assert marker not in serialized
        assert "internal_table.secret_column" not in serialized
        assert "private-value" not in serialized
        assert "retry_feedback" not in serialized
