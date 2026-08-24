"""엔티티 확정 -> 라우팅 -> 쿼리 생성의 전체 흐름을 테스트한다."""

from orchestrator.graph import build_orchestrator_graph
from tests.mocks.openai import (
    MockOpenAIClient,
    make_content_response,
    make_tool_call_response,
)
from tests.mocks.postgres import MockPostgresConnection


def test_graph_resolves_entity_then_routes_to_sql() -> None:
    """제품명이 있는 SQL형 질의는 entity 확정 후 sql로 라우팅된다."""
    openai_client = MockOpenAIClient(
        make_tool_call_response(
            "extract_product_name", {"productName": "Touring-1000 Yellow, 54"}
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
    assert result["cypher_query"] is None


def test_graph_routes_to_graph_for_relationship_query() -> None:
    """부품 사용처를 묻는 질의는 entity 확정 후 graph로 라우팅된다."""
    openai_client = MockOpenAIClient(
        make_tool_call_response(
            "extract_product_name", {"productName": "Paint - Black"}
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
    assert result["cypher_query"] == (
        "MATCH (part:Product)<-[:REQUIRES_COMPONENT*1..4]-(parent:Product) "
        "WHERE part.productId = 492 RETURN parent"
    )


def test_graph_generates_sql_without_entity_for_aggregate_query() -> None:
    """특정 제품을 지칭하지 않는 집계 질의도 SQL을 생성한다."""
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
    assert result["cypher_query"] is None


def test_graph_normalizes_synonyms_before_existing_nodes() -> None:
    """동의어는 표준 용어로 바뀐 뒤 기존 엔티티·라우팅·생성 노드로 전달된다."""
    openai_client = MockOpenAIClient(
        make_content_response("[]"),
        make_content_response('["graph"]'),
        make_content_response(
            "MATCH (s:Supplier)-[:SUPPLIES]->(p:Product) RETURN s, p"
        ),
    )
    graph = build_orchestrator_graph(
        openai_client, MockPostgresConnection(rows_by_name={})
    )

    result = graph.invoke({"query": "협력사가 공급하는 자재를 보여줘."})

    assert result["query"] == "협력사가 공급하는 자재를 보여줘."
    assert result["normalized_query"] == "공급업체가 공급하는 부품을 보여줘."
    assert result["tool_plan"] == ["graph"]
    assert result["query_guard"]["decision"] == "PASSED"
    assert result["execution_allowed"] is True
    assert (
        "공급업체가 공급하는 부품" in openai_client.calls[0]["messages"][1]["content"]
    )


def test_graph_stops_before_llm_when_user_requests_write() -> None:
    """명확한 쓰기 요청은 기존 LLM 기반 노드를 호출하지 않는다."""
    openai_client = MockOpenAIClient()
    graph = build_orchestrator_graph(
        openai_client, MockPostgresConnection(rows_by_name={})
    )

    result = graph.invoke({"query": "모든 제품을 삭제해줘."})

    assert result["natural_guard"]["decision"] == "BLOCK_WRITE"
    assert result["execution_allowed"] is False
    assert result.get("tool_plan") is None
    assert openai_client.calls == []
