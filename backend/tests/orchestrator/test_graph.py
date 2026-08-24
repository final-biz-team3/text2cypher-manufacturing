"""엔티티 확정 -> 라우팅 -> self-correction 뼈대까지의 전체 흐름을 테스트한다."""

from orchestrator.graph import build_orchestrator_graph
from tests.mocks.openai import (
    MockOpenAIClient,
    make_content_response,
    make_tool_call_response,
)
from tests.mocks.postgres import MockPostgresConnection


def test_graph_resolves_entity_then_runs_sql_agent_once() -> None:
    """제품명이 있는 SQL형 질의는 entity 확정 후 sql_agent가 한 번 생성·실행을
    시도한다(execute_sql이 자리표시라 항상 실패하고 error에 담긴다)."""
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
    postgres_connection = MockPostgresConnection(
        rows_by_name={"Touring-1000 Yellow, 54": (956, "Touring-1000 Yellow, 54")}
    )
    graph = build_orchestrator_graph(openai_client, postgres_connection)

    result = graph.invoke(
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
    assert result["sql_result"]["result"] is None
    assert "self-correction 구현에서 채운다" in result["sql_result"]["error"]
    assert result["cypher_query"] is None
    assert result["graph_result"] is None
    assert len(openai_client.calls) == 3


def test_graph_routes_to_graph_and_runs_cypher_agent_once() -> None:
    """부품 사용처를 묻는 질의는 entity 확정 후 graph로 라우팅되고 cypher_agent가
    한 번 생성·실행을 시도한다."""
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
    postgres_connection = MockPostgresConnection(
        rows_by_name={"Paint - Black": (492, "Paint - Black")}
    )
    graph = build_orchestrator_graph(openai_client, postgres_connection)

    result = graph.invoke(
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
    assert result["graph_result"]["result"] is None
    assert "self-correction 구현에서 채운다" in result["graph_result"]["error"]
    assert len(openai_client.calls) == 3


def test_graph_builds_final_answer_from_sql_result() -> None:
    """특정 제품을 지칭하지 않는 집계 질의도 sql_agent를 거쳐 final_answer가 채워진다."""
    openai_client = MockOpenAIClient(
        make_content_response("[]"),
        make_content_response('["sql"]'),
        make_content_response("SELECT COUNT(*) FROM production.product"),
    )
    postgres_connection = MockPostgresConnection(rows_by_name={})
    graph = build_orchestrator_graph(openai_client, postgres_connection)

    result = graph.invoke({"query": "전체 제품 수를 알려줘."})

    assert result["entity"] is None
    assert result["tool_plan"] == ["sql"]
    assert result["sql_query"] == "SELECT COUNT(*) FROM production.product"
    assert "self-correction 구현에서 채운다" in result["final_answer"]
