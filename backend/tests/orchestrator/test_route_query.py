"""route_query 노드가 질의를 SQL·GRAPH 실행 계획으로 분류하는 동작을 테스트한다."""

import pytest

from orchestrator.nodes.route_query import RoutePlanError, make_route_query_node
from tests.mocks.openai import MockOpenAIClient, make_content_response


def test_route_query_returns_sql_tool_plan_for_numeric_query() -> None:
    """수치 조회 질의는 ["sql"]로 라우팅된다."""
    openai_client = MockOpenAIClient(make_content_response('["sql"]'))
    node = make_route_query_node(openai_client)

    result = node(
        {
            "query": "Touring-1000 Yellow, 54의 정가와 표준원가를 알려줘.",
            "entity": {"productId": 956, "productName": "Touring-1000 Yellow, 54"},
        }
    )

    assert result == {
        "tool_plan": ["sql"],
        "subqueries": [
            {
                "id": "sql_query",
                "tool": "sql",
                "question": "Touring-1000 Yellow, 54의 정가와 표준원가를 알려줘.",
                "dependsOn": [],
                "requiredOutputs": [],
                "joinKeys": [],
            }
        ],
    }


def test_route_query_returns_graph_tool_plan_for_relationship_query() -> None:
    """다단계 관계 탐색 질의는 ["graph"]로 라우팅된다."""
    openai_client = MockOpenAIClient(make_content_response('["graph"]'))
    node = make_route_query_node(openai_client)

    result = node(
        {
            "query": "부품 Paint - Black을 사용하는 완제품을 최대 4단계까지 알려줘.",
            "entity": {"productId": 492, "productName": "Paint - Black"},
        }
    )

    assert result == {
        "tool_plan": ["graph"],
        "subqueries": [
            {
                "id": "graph_query",
                "tool": "graph",
                "question": "부품 Paint - Black을 사용하는 완제품을 최대 4단계까지 알려줘.",
                "dependsOn": [],
                "requiredOutputs": [],
                "joinKeys": [],
            }
        ],
    }


def test_route_query_sends_query_and_entity_in_prompt() -> None:
    """LLM에 보내는 프롬프트에 질의 원문과 확정된 entity를 포함한다."""
    openai_client = MockOpenAIClient(make_content_response('["sql"]'))
    node = make_route_query_node(openai_client)

    node(
        {
            "query": "활성 공급업체 수를 알려줘.",
            "entity": None,
        }
    )

    sent_messages = openai_client.calls[0]["messages"]
    assert openai_client.calls[0]["response_format"] == {"type": "json_object"}
    system_message = next(m["content"] for m in sent_messages if m["role"] == "system")
    assert "다른 단계로 전달하거나 최종 결합에 실제로 필요한 필드만" in system_message
    assert "전달·결합이 없으면 빈 배열" in system_message
    assert "requiredOutputs와 joinKeys 둘 다에" in system_message
    user_message = next(m["content"] for m in sent_messages if m["role"] == "user")
    assert "활성 공급업체 수를 알려줘." in user_message


def test_route_query_requires_openai_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """OPENAI_MODEL이 없으면 라우팅 요청 전에 즉시 실패한다."""
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    openai_client = MockOpenAIClient(make_content_response('["sql"]'))
    node = make_route_query_node(openai_client)

    with pytest.raises(KeyError, match="OPENAI_MODEL"):
        node({"query": "제품의 정가를 알려줘.", "entity": None})

    assert openai_client.calls == []


@pytest.mark.parametrize(
    "tool_plan_json",
    ["[]", '["unknown"]', '"sql"'],
)
def test_route_query_rejects_invalid_plan(tool_plan_json: str) -> None:
    """빈 계획, 지원하지 않는 도구, 리스트가 아닌 값은 거부한다."""
    openai_client = MockOpenAIClient(make_content_response(tool_plan_json))
    node = make_route_query_node(openai_client)

    with pytest.raises(RoutePlanError) as exc_info:
        node({"query": "질의", "entity": None})

    assert exc_info.value.raw_response == tool_plan_json
