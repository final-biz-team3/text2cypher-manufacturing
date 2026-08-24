"""라우팅 계획을 언어별 쿼리 생성기에 연결하는 노드를 테스트한다."""

import pytest

from agents.cypher.schema.models import GraphQueryPolicy
from orchestrator.nodes.generate_queries import make_generate_queries_node
from tests.mocks.openai import MockOpenAIClient, make_content_response

SQL_SCHEMA_TEXT = "production.product {productid: INTEGER, listprice: NUMERIC}"
CYPHER_SCHEMA_TEXT = "(:Product)-[:REQUIRES_COMPONENT]->(:Product)"
CYPHER_QUERY_POLICY = GraphQueryPolicy(
    bomAsOfDate="2014-08-08",
    bomMaxDepth=4,
)


def _make_node(*responses: str):
    client = MockOpenAIClient(*(make_content_response(value) for value in responses))
    node = make_generate_queries_node(
        client,
        sql_schema_text=SQL_SCHEMA_TEXT,
        cypher_schema_text=CYPHER_SCHEMA_TEXT,
        cypher_query_policy=CYPHER_QUERY_POLICY,
    )
    return node, client


def test_generate_queries_runs_only_sql_for_sql_plan() -> None:
    """sql 계획은 SQL만 생성하고 Cypher는 비워 둔다."""
    node, client = _make_node("SELECT listprice FROM production.product")

    result = node(
        {
            "query": "이 제품의 정가를 알려줘.",
            "entity": {"productId": 956},
            "tool_plan": ["sql"],
        }
    )

    assert result == {
        "sql_query": "SELECT listprice FROM production.product",
        "cypher_query": None,
    }
    assert len(client.calls) == 1
    assert "Table schemas" not in client.calls[0]["messages"][0]["content"]
    assert SQL_SCHEMA_TEXT in client.calls[0]["messages"][0]["content"]


def test_generate_queries_runs_only_cypher_for_graph_plan() -> None:
    """graph 계획은 Cypher만 생성하고 SQL은 비워 둔다."""
    cypher = "MATCH (part:Product)<-[:REQUIRES_COMPONENT*1..4]-(parent) RETURN parent"
    node, client = _make_node(cypher)

    result = node(
        {
            "query": "이 부품을 사용하는 완제품을 4단계까지 알려줘.",
            "entity": {"productId": 492},
            "tool_plan": ["graph"],
        }
    )

    assert result == {"sql_query": None, "cypher_query": cypher}
    assert len(client.calls) == 1
    assert CYPHER_SCHEMA_TEXT in client.calls[0]["messages"][0]["content"]


def test_generate_queries_supports_combined_plan_without_query_specific_branch() -> (
    None
):
    """두 도구 계획도 같은 노드에서 순서대로 생성한다."""
    node, client = _make_node("SELECT 1", "MATCH (n) RETURN n")

    result = node({"query": "복합 질의", "entity": None, "tool_plan": ["sql", "graph"]})

    assert result == {
        "sql_query": "SELECT 1",
        "cypher_query": "MATCH (n) RETURN n",
    }
    assert len(client.calls) == 2


@pytest.mark.parametrize("tool_plan", [[], ["unknown"]])
def test_generate_queries_rejects_invalid_plan(tool_plan: list[str]) -> None:
    """빈 계획과 지원하지 않는 도구는 LLM 호출 전에 거부한다."""
    node, client = _make_node()

    with pytest.raises(ValueError):
        node({"query": "질의", "entity": None, "tool_plan": tool_plan})

    assert client.calls == []
