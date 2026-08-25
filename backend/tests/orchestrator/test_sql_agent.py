"""SQL Agent SubGraph의 생성-실행 뼈대를 테스트한다."""

from orchestrator.subgraphs.sql_agent import make_sql_agent_subgraph
from tests.mocks.openai import MockOpenAIClient, make_content_response


def _initial_state(query: str = "제품 수를 알려줘.") -> dict:
    return {
        "query": query,
        "entity": None,
        "schema": "production.product {productid: INTEGER}",
        "messages": [],
        "result": None,
        "error": None,
    }


def test_sql_agent_returns_result_when_execution_succeeds() -> None:
    """실행이 성공하면 result를 채우고 error는 None이다."""
    openai_client = MockOpenAIClient(
        make_content_response("SELECT COUNT(*) FROM production.product")
    )
    subgraph = make_sql_agent_subgraph(
        openai_client, execute_sql=lambda sql: [{"count": 10}]
    )

    result = subgraph.invoke(_initial_state())

    assert result["result"] == [{"count": 10}]
    assert result["error"] is None
    assert (
        result["messages"][-1]["content"] == "SELECT COUNT(*) FROM production.product"
    )
    assert len(openai_client.calls) == 1


def test_sql_agent_returns_error_when_execution_fails() -> None:
    """실행이 실패하면 예외를 전파하지 않고 error 필드에 담아 정상 종료한다."""
    openai_client = MockOpenAIClient(
        make_content_response("SELECT bad_column FROM production.product")
    )

    def execute_sql(sql: str) -> None:
        raise ValueError("column bad_column does not exist")

    subgraph = make_sql_agent_subgraph(openai_client, execute_sql=execute_sql)

    result = subgraph.invoke(_initial_state())

    assert result["result"] is None
    assert result["error"] == "column bad_column does not exist"
    assert len(openai_client.calls) == 1
