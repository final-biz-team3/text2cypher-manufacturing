"""resolve_entity -> route_query 2노드 그래프의 전체 흐름을 테스트한다."""

from orchestrator.graph import build_orchestrator_graph
from tests.orchestrator.fakes import (
    FakeOpenAIClient,
    FakePostgresConnection,
    make_content_response,
    make_tool_call_response,
)


def test_graph_resolves_entity_then_routes_to_sql() -> None:
    """제품명이 있는 SQL형 질의는 entity 확정 후 sql로 라우팅된다."""
    openai_client = FakeOpenAIClient(
        make_tool_call_response(
            "extract_product_name", {"productName": "Touring-1000 Yellow, 54"}
        ),
        make_content_response('["sql"]'),
    )
    postgres_connection = FakePostgresConnection(
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


def test_graph_routes_to_graph_for_relationship_query() -> None:
    """부품 사용처를 묻는 질의는 entity 확정 후 graph로 라우팅된다."""
    openai_client = FakeOpenAIClient(
        make_tool_call_response(
            "extract_product_name", {"productName": "Paint - Black"}
        ),
        make_content_response('["graph"]'),
    )
    postgres_connection = FakePostgresConnection(
        rows_by_name={"Paint - Black": (492, "Paint - Black")}
    )
    graph = build_orchestrator_graph(openai_client, postgres_connection)

    result = graph.invoke(
        {"query": "부품 Paint - Black을 사용하는 완제품을 최대 4단계까지 알려줘."}
    )

    assert result["entity"] == {"productId": 492, "productName": "Paint - Black"}
    assert result["tool_plan"] == ["graph"]


def test_graph_routes_without_entity_for_aggregate_query() -> None:
    """특정 제품을 지칭하지 않는 집계 질의는 entity=None으로 sql 라우팅된다."""
    openai_client = FakeOpenAIClient(
        make_content_response("[]"),
        make_content_response('["sql"]'),
    )
    postgres_connection = FakePostgresConnection(rows_by_name={})
    graph = build_orchestrator_graph(openai_client, postgres_connection)

    result = graph.invoke({"query": "현재 활성 상태인 공급업체 수를 알려줘."})

    assert result["entity"] is None
    assert result["tool_plan"] == ["sql"]
