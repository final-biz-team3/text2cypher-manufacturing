"""Cypher Agent SubGraph의 생성-실행 뼈대를 테스트한다."""

from agents.cypher.schema.models import GraphQueryPolicy
from orchestrator.subgraphs.cypher_agent import make_cypher_agent_subgraph
from tests.mocks.openai import MockOpenAIClient, make_content_response

QUERY_POLICY = GraphQueryPolicy(bomAsOfDate="2014-08-08", bomMaxDepth=4)


def _initial_state(query: str = "부품 사용처를 알려줘.") -> dict:
    return {
        "query": query,
        "entity": None,
        "schema": "(:Product)-[:REQUIRES_COMPONENT]->(:Product)",
        "messages": [],
        "result": None,
        "error": None,
    }


def test_cypher_agent_returns_result_when_execution_succeeds() -> None:
    """실행이 성공하면 result를 채우고 error는 None이다."""
    openai_client = MockOpenAIClient(
        make_content_response("MATCH (n:Product) RETURN n")
    )
    subgraph = make_cypher_agent_subgraph(
        openai_client,
        execute_cypher=lambda cypher: [{"n": "x"}],
        query_policy=QUERY_POLICY,
    )

    result = subgraph.invoke(_initial_state())

    assert result["result"] == [{"n": "x"}]
    assert result["error"] is None
    assert result["messages"][-1]["content"] == "MATCH (n:Product) RETURN n"
    assert len(openai_client.calls) == 1


def test_cypher_agent_returns_error_when_execution_fails() -> None:
    """실행이 실패하면 예외를 전파하지 않고 error 필드에 담아 정상 종료한다."""
    openai_client = MockOpenAIClient(
        make_content_response("MATCH (n:Unknown) RETURN n")
    )

    def execute_cypher(cypher: str) -> None:
        raise ValueError("unknown label Unknown")

    subgraph = make_cypher_agent_subgraph(
        openai_client, execute_cypher=execute_cypher, query_policy=QUERY_POLICY
    )

    result = subgraph.invoke(_initial_state())

    assert result["result"] is None
    assert result["error"] == "unknown label Unknown"
    assert len(openai_client.calls) == 1
