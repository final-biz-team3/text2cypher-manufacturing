"""Cypher Agent SubGraph의 생성-실행과 self-correction 재시도를 테스트한다."""

from neo4j.exceptions import CypherSyntaxError, ServiceUnavailable

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


def test_cypher_agent_retries_after_retryable_error_then_succeeds() -> None:
    """실행 오류(화이트리스트)가 나면 쿼리를 재생성해 재시도하고, 성공하면 종료한다."""
    openai_client = MockOpenAIClient(
        make_content_response("MATCH (n:Unknown) RETURN n"),
        make_content_response("MATCH (n:Product) RETURN n"),
    )
    calls = []

    def execute_cypher(cypher: str):
        calls.append(cypher)
        if len(calls) == 1:
            raise CypherSyntaxError("Invalid input 'Unknown'")
        return [{"n": "x"}]

    subgraph = make_cypher_agent_subgraph(
        openai_client, execute_cypher=execute_cypher, query_policy=QUERY_POLICY
    )

    result = subgraph.invoke(_initial_state())

    assert result["result"] == [{"n": "x"}]
    assert result["error"] is None
    assert len(openai_client.calls) == 2
    assert len(result["attempts"]) == 2


def test_cypher_agent_does_not_retry_on_connection_error() -> None:
    """접속(인프라) 오류는 쿼리를 재생성해도 해결되지 않으므로 재시도하지 않는다."""
    openai_client = MockOpenAIClient(
        make_content_response("MATCH (n:Product) RETURN n")
    )

    def execute_cypher(cypher: str):
        raise ServiceUnavailable("could not connect to server")

    subgraph = make_cypher_agent_subgraph(
        openai_client, execute_cypher=execute_cypher, query_policy=QUERY_POLICY
    )

    result = subgraph.invoke(_initial_state())

    assert result["result"] is None
    assert result["error"] == "could not connect to server"
    assert len(openai_client.calls) == 1


def test_cypher_agent_stops_after_max_attempts_exceeded() -> None:
    """실행 오류가 계속되면 원본 1회 + 재시도 2회(총 3회)까지만 시도하고 종료한다."""
    openai_client = MockOpenAIClient(
        make_content_response("MATCH (n:A) RETURN n"),
        make_content_response("MATCH (n:B) RETURN n"),
        make_content_response("MATCH (n:C) RETURN n"),
    )

    def execute_cypher(cypher: str):
        raise CypherSyntaxError("invalid syntax")

    subgraph = make_cypher_agent_subgraph(
        openai_client, execute_cypher=execute_cypher, query_policy=QUERY_POLICY
    )

    result = subgraph.invoke(_initial_state())

    assert result["result"] is None
    assert result["error"] == "invalid syntax"
    assert result["attempt_count"] == 3
    assert len(openai_client.calls) == 3
    assert len(result["attempts"]) == 3


def test_cypher_agent_retries_once_on_empty_result_then_accepts() -> None:
    """빈 결과는 1회만 재시도하고, 재시도 후에도 비면 정답으로 받아들인다."""
    openai_client = MockOpenAIClient(
        make_content_response("MATCH (n:Product {id: -1}) RETURN n"),
        make_content_response("MATCH (n:Product {id: -1}) RETURN n"),
    )

    def execute_cypher(cypher: str):
        return []

    subgraph = make_cypher_agent_subgraph(
        openai_client, execute_cypher=execute_cypher, query_policy=QUERY_POLICY
    )

    result = subgraph.invoke(_initial_state())

    assert result["result"] == []
    assert result["error"] is None
    assert result["empty_reason"] == "NO_DATA"
    assert len(openai_client.calls) == 2


def test_cypher_agent_marks_empty_result_inconclusive_after_budget_exhausted() -> None:
    """실행 오류로 재시도 예산을 다 쓴 뒤 마지막 시도가 빈 결과면, 빈 결과를
    재시도해볼 기회조차 없었으므로 정답으로 확신하지 않고 INCONCLUSIVE로
    표시한다(그리고 내부용 EMPTY_RESULT 문자열이 error로 새어나가면 안 된다)."""
    openai_client = MockOpenAIClient(
        make_content_response("MATCH (n:A) RETURN n"),
        make_content_response("MATCH (n:B) RETURN n"),
        make_content_response("MATCH (n:Product {id: -1}) RETURN n"),
    )
    calls = []

    def execute_cypher(cypher: str):
        calls.append(cypher)
        if len(calls) <= 2:
            raise CypherSyntaxError("invalid syntax")
        return []

    subgraph = make_cypher_agent_subgraph(
        openai_client, execute_cypher=execute_cypher, query_policy=QUERY_POLICY
    )

    result = subgraph.invoke(_initial_state())

    assert result["result"] == []
    assert result["error"] is None
    assert result["empty_reason"] == "INCONCLUSIVE"
    assert result["attempt_count"] == 3
    assert len(result["attempts"]) == 3
