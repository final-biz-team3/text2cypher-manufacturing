"""엔티티 확정 -> 라우팅 -> 실제 SQL/Cypher 실행까지의 전체 흐름을 테스트한다.
execute_sql/execute_cypher가 이제 진짜 DB를 치므로 이 파일은 전부 integration
마커가 붙는다(OpenAI 호출만 mock, DB는 실제)."""

from decimal import Decimal

import pytest
import pytest_asyncio
from dotenv import load_dotenv

# core.postgres/orchestrator.execution.*는 os.getenv()로 환경변수를 직접
# 읽고 load_dotenv()를 호출하지 않는다(main.py만 호출한다) - main.py를
# import하지 않는 이 테스트는 여기서 직접 .env를 로드해야 한다.
load_dotenv()

from core.postgres import bootstrap_postgres, get_pool, open_pool  # noqa: E402
from orchestrator.graph import build_orchestrator_graph  # noqa: E402
from tests.mocks.openai import (  # noqa: E402
    MockOpenAIClient,
    make_content_response,
    make_tool_call_response,
)

# execute_cypher가 Neo4j reader 드라이버 싱글턴을 모듈 레벨 전역으로 갖고
# 있어서, 테스트 함수마다 기본(function scope)으로 다른 이벤트 루프를 쓰면
# 한 테스트에서 만든 드라이버를 다른 테스트(다른 루프)가 재사용하려다
# "attached to a different loop" RuntimeError가 난다(실측으로 확인함 -
# Postgres AsyncConnectionPool은 우연히 버텼지만 Neo4j 소켓 계층은 안 버팀).
# 이 파일의 테스트와 fixture 전체를 하나의 module 스코프 이벤트 루프로
# 묶어서 근본적으로 막는다.
pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def postgres_pool():
    """execute_sql/resolve_entity가 공유하는 앱 전역 read pool을 연다."""
    await bootstrap_postgres()
    await open_pool()
    return get_pool()


async def test_graph_resolves_entity_then_runs_sql_agent_once(postgres_pool) -> None:
    """제품명이 있는 SQL형 질의는 entity 확정 후 sql_agent가 실제 DB 결과를 받는다."""
    openai_client = MockOpenAIClient(
        make_tool_call_response(
            "extract_entity",
            {"entityType": "product", "entityName": "Touring-1000 Yellow, 54"},
        ),
        make_content_response('["sql"]'),
        make_content_response(
            "SELECT listprice, standardcost FROM production.product "
            "WHERE productid = 956"
        ),
    )
    graph = build_orchestrator_graph(openai_client, postgres_pool)

    result = await graph.ainvoke(
        {"query": "Touring-1000 Yellow, 54의 정가와 표준원가를 알려줘."}
    )

    assert result["entity"] == {
        "productId": 956,
        "productName": "Touring-1000 Yellow, 54",
    }
    assert result["tool_plan"] == ["sql"]
    assert result["sql_query"] == (
        "SELECT listprice, standardcost FROM production.product "
        "WHERE productid = 956"
    )
    assert result["sql_result"]["error"] is None
    assert result["sql_result"]["result"] == [
        {"listprice": Decimal("2384.07"), "standardcost": Decimal("1481.9379")}
    ]
    assert result["cypher_query"] is None
    assert result["graph_result"] is None
    assert len(openai_client.calls) == 3


async def test_graph_routes_to_graph_and_runs_cypher_agent_once(postgres_pool) -> None:
    """부품 사용처를 묻는 질의는 entity 확정 후 graph로 라우팅되고 cypher_agent가
    실제 Neo4j 결과를 받는다."""
    openai_client = MockOpenAIClient(
        make_tool_call_response(
            "extract_entity", {"entityType": "product", "entityName": "Paint - Black"}
        ),
        make_content_response('["graph"]'),
        make_content_response(
            "MATCH (part:Product)<-[:REQUIRES_COMPONENT*1..4]-(parent:Product) "
            "WHERE part.productId = 492 RETURN parent"
        ),
    )
    graph = build_orchestrator_graph(openai_client, postgres_pool)

    result = await graph.ainvoke(
        {"query": "부품 Paint - Black을 사용하는 완제품을 최대 4단계까지 알려줘."}
    )

    assert result["entity"] == {"productId": 492, "productName": "Paint - Black"}
    assert result["tool_plan"] == ["graph"]
    assert result["sql_query"] is None
    assert result["sql_result"] is None
    assert result["cypher_query"] == (
        "MATCH (part:Product)<-[:REQUIRES_COMPONENT*1..4]-(parent:Product) "
        "WHERE part.productId = 492 RETURN parent"
    )
    assert result["graph_result"]["error"] is None
    graph_rows = result["graph_result"]["result"]
    assert graph_rows
    assert any(row["parent"]["productId"] == 680 for row in graph_rows)


async def test_graph_runs_both_agents_independently_for_hybrid_tool_plan(
    postgres_pool,
) -> None:
    """tool_plan이 ["sql", "graph"] Hybrid일 때 sql_agent와 cypher_agent가 각각
    독립적으로 실행되고, 한쪽의 attempts/error가 다른 쪽으로 섞이지 않는다."""
    openai_client = MockOpenAIClient(
        make_tool_call_response(
            "extract_entity",
            {"entityType": "product", "entityName": "Touring-1000 Yellow, 54"},
        ),
        make_content_response('["sql", "graph"]'),
        make_content_response(
            "SELECT listprice, standardcost FROM production.product "
            "WHERE productid = 956"
        ),
        make_content_response(
            "MATCH (finished:Product {productId: 956})-[:REQUIRES_COMPONENT]->"
            "(component:Product) RETURN component"
        ),
    )
    graph = build_orchestrator_graph(openai_client, postgres_pool)

    result = await graph.ainvoke(
        {
            "query": (
                "Touring-1000 Yellow, 54의 정가와 표준원가, "
                "그리고 이 제품에 들어가는 부품을 알려줘."
            )
        }
    )

    assert result["tool_plan"] == ["sql", "graph"]

    assert result["sql_query"] == (
        "SELECT listprice, standardcost FROM production.product "
        "WHERE productid = 956"
    )
    assert result["sql_result"]["error"] is None
    assert result["sql_result"]["result"] == [
        {"listprice": Decimal("2384.07"), "standardcost": Decimal("1481.9379")}
    ]
    assert len(result["sql_result"]["attempts"]) == 1

    assert result["cypher_query"] == (
        "MATCH (finished:Product {productId: 956})-[:REQUIRES_COMPONENT]->"
        "(component:Product) RETURN component"
    )
    assert result["graph_result"]["error"] is None
    assert result["graph_result"]["result"]
    assert len(result["graph_result"]["attempts"]) == 1

    assert len(openai_client.calls) == 4


async def test_graph_builds_final_answer_from_sql_result(postgres_pool) -> None:
    """특정 제품을 지칭하지 않는 집계 질의도 sql_agent를 거쳐 final_answer가 채워진다."""
    openai_client = MockOpenAIClient(
        make_content_response("[]"),
        make_content_response('["sql"]'),
        make_content_response("SELECT COUNT(*) FROM production.product"),
    )
    graph = build_orchestrator_graph(openai_client, postgres_pool)

    result = await graph.ainvoke({"query": "전체 제품 수를 알려줘."})

    assert result["entity"] is None
    assert result["tool_plan"] == ["sql"]
    assert result["sql_query"] == "SELECT COUNT(*) FROM production.product"
    assert result["sql_result"]["error"] is None
    assert result["sql_result"]["result"] == [{"count": 504}]
    assert result["final_answer"] is not None
    assert "504" in result["final_answer"]
