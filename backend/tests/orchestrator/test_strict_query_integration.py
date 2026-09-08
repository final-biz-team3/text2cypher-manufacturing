"""외부 모델 대신 고정 응답을 사용해 실제 DB와 이식한 경로를 연결한다."""

import json
from decimal import Decimal

import pytest
import pytest_asyncio

from core.postgres import close_pool, get_pool, open_pool
from orchestrator.graph import build_orchestrator_graph
from strict_query.pipeline import build_strict_query
from tests.mocks.openai import (
    MockOpenAIClient,
    make_content_response,
    make_no_tool_call_response,
    make_tool_call_response,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def pool():
    await open_pool()
    yield get_pool()
    await close_pool()


async def test_strict_sql_reaches_unchanged_pr61_answer(pool):
    client = MockOpenAIClient(
        make_content_response("ON_TOPIC"),
        make_tool_call_response(
            "extract_entity",
            {"entityType": "product", "entityName": "Touring-1000 Yellow, 54"},
        ),
        make_content_response('["sql"]'),
        make_content_response('{"requiredOutputs":["listPrice","standardCost"]}'),
        make_content_response(
            'SELECT productid AS "productId", name AS "productName", listprice AS "listPrice", standardcost AS "standardCost" FROM production.product WHERE productid = 956'
        ),
        make_content_response(
            json.dumps(
                {
                    "highlighted": [
                        {
                            "title": "Touring-1000 Yellow, 54",
                            "metrics": [{"label": "정가", "value": 2384.07}],
                        }
                    ],
                    "sections": [],
                }
            )
        ),
    )
    state = await build_orchestrator_graph(client, pool).ainvoke(
        {"query": "Touring-1000 Yellow, 54의 정가와 표준원가를 알려줘."}
    )
    assert state["query_strategy"] == "strict"
    assert state["strict_attempt"]["status"] == "accepted"
    assert state["composed_result"]["rows"][0]["listPrice"] == Decimal("2384.07")
    assert state["answer_metadata"]["mode"] == "structured"
    assert len(client.calls) == 6


async def test_strict_graph_uses_current_reader(pool):
    from orchestrator.execution.cypher_executor import close_reader_driver

    client = MockOpenAIClient(
        make_no_tool_call_response(),
        make_content_response('["graph"]'),
        make_content_response('{"requiredOutputs":["productId"]}'),
        make_content_response(
            "MATCH (p:Product) RETURN p.productId AS productId, p.name AS productName ORDER BY productId LIMIT 1"
        ),
    )
    try:
        state = await build_strict_query(client, pool)(
            {"query": "등록된 제품 식별자 하나를 보여줘."}
        )
        assert state["composed_result"]["error"] is None
        assert state["composed_result"]["rows"] == [
            {"productId": 1, "productName": "Adjustable Race"}
        ]
        assert "final_answer" not in state
        assert len(client.calls) == 4
    finally:
        await close_reader_driver()
