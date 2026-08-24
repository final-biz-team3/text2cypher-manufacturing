"""resolve_entity 노드가 제품명을 확정하거나 통과시키는 동작을 테스트한다."""

import pytest

from orchestrator.errors import EntityNotFoundError
from orchestrator.nodes.resolve_entity import make_resolve_entity_node
from tests.mocks.openai import (
    MockOpenAIClient,
    make_no_tool_call_response,
    make_tool_call_response,
)
from tests.mocks.postgres import MockPostgresConnection


def test_resolve_entity_returns_entity_when_product_found() -> None:
    """질의에서 추출한 제품명이 DB에 있으면 productId를 확정한다."""
    openai_client = MockOpenAIClient(
        make_tool_call_response(
            "extract_product_name",
            {"productName": "Touring-1000 Yellow, 54"},
        )
    )
    postgres_connection = MockPostgresConnection(
        rows_by_name={"Touring-1000 Yellow, 54": (956, "Touring-1000 Yellow, 54")}
    )
    node = make_resolve_entity_node(openai_client, postgres_connection)

    result = node({"query": "Touring-1000 Yellow, 54의 정가와 표준원가를 알려줘."})

    assert result == {
        "entity": {"productId": 956, "productName": "Touring-1000 Yellow, 54"}
    }
    assert openai_client.calls[0]["reasoning_effort"] == "none"


def test_resolve_entity_returns_none_entity_when_no_product_mentioned() -> None:
    """제품을 지칭하지 않는 질의는 DB 조회 없이 entity=None으로 통과한다."""
    openai_client = MockOpenAIClient(make_no_tool_call_response())
    postgres_connection = MockPostgresConnection(rows_by_name={})
    node = make_resolve_entity_node(openai_client, postgres_connection)

    result = node({"query": "현재 활성 상태인 공급업체 수를 알려줘."})

    assert result == {"entity": None}
    assert postgres_connection.last_query is None


def test_resolve_entity_raises_when_product_not_found() -> None:
    """추출된 제품명이 DB에 없으면 EntityNotFoundError를 발생시킨다."""
    openai_client = MockOpenAIClient(
        make_tool_call_response(
            "extract_product_name", {"productName": "존재하지 않는 제품"}
        )
    )
    postgres_connection = MockPostgresConnection(rows_by_name={})
    node = make_resolve_entity_node(openai_client, postgres_connection)

    with pytest.raises(EntityNotFoundError):
        node({"query": "존재하지 않는 제품의 정가를 알려줘."})


def test_resolve_entity_requires_openai_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """OPENAI_MODEL이 없으면 제품명 추출 요청 전에 즉시 실패한다."""
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    openai_client = MockOpenAIClient(make_no_tool_call_response())
    node = make_resolve_entity_node(
        openai_client,
        MockPostgresConnection(rows_by_name={}),
    )

    with pytest.raises(KeyError, match="OPENAI_MODEL"):
        node({"query": "제품의 정가를 알려줘."})

    assert openai_client.calls == []
