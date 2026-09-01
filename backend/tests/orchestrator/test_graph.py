"""Production graph integration without evaluation-family fixtures."""

import json
import os

import pytest
import pytest_asyncio
from dotenv import load_dotenv

load_dotenv()

from core.postgres import bootstrap_postgres, get_pool, open_pool  # noqa: E402
from orchestrator.graph import build_orchestrator_graph  # noqa: E402
from tests.mocks.openai import (  # noqa: E402
    MockChatCompletion,
    MockOpenAIClient,
    make_content_response,
    make_no_tool_call_response,
    make_output_plan_response,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]
_ANSWER = "false"


def _route(tool: str) -> str:
    return json.dumps(
        {
            "subqueries": [
                {
                    "id": f"{tool}_facts",
                    "tool": tool,
                    "question": f"등록된 {tool} 사실을 한 건 조회한다.",
                    "dependsOn": [],
                    "joinKeys": [],
                    "inputBindings": [],
                }
            ],
            "resultTransform": None,
        },
        ensure_ascii=False,
    )


def _client(*responses: MockChatCompletion) -> MockOpenAIClient:
    return MockOpenAIClient(*responses, make_content_response(_ANSWER))


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def postgres_pool():
    await bootstrap_postgres()
    await open_pool()
    return get_pool()


async def test_production_graph_executes_read_only_sql(postgres_pool) -> None:
    client = _client(
        make_no_tool_call_response(),
        make_content_response(_route("sql")),
        make_output_plan_response(answer_values=["productCount"]),
        make_content_response(
            'SELECT COUNT(*) AS "productCount" FROM production.product'
        ),
    )

    result = await build_orchestrator_graph(client, postgres_pool).ainvoke(
        {"query": "등록된 제품의 전체 개수를 알려줘."}
    )

    assert result["entity"] is None
    assert result["tool_plan"] == ["sql"]
    assert result["sql_result"]["error"] is None
    assert result["sql_result"]["result"][0]["productCount"] > 0
    assert result["cypher_query"] is None
    assert result["final_answer"] == _ANSWER


async def test_production_graph_executes_read_only_cypher(postgres_pool) -> None:
    if not os.getenv("NEO4J_READER_USER") or not os.getenv("NEO4J_READER_PASSWORD"):
        pytest.skip("NEO4J_READER_USER/PASSWORD are not configured")
    client = _client(
        make_no_tool_call_response(),
        make_content_response(_route("graph")),
        make_output_plan_response(answer_values=["productId"]),
        make_content_response(
            "MATCH (p:Product) RETURN p.productId AS productId "
            "ORDER BY productId LIMIT 1"
        ),
    )

    result = await build_orchestrator_graph(client, postgres_pool).ainvoke(
        {"query": "등록된 제품 식별자 하나를 보여줘."}
    )

    assert result["entity"] is None
    assert result["tool_plan"] == ["graph"]
    assert result["graph_result"]["error"] is None
    assert result["graph_result"]["result"]
    assert result["sql_query"] is None
    assert result["final_answer"] == _ANSWER
