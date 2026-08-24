"""resolve_entity 노드가 엔티티를 확정하거나 통과시키는 동작을 테스트한다."""

import pytest

from agents.cypher.schema.models import GraphSchema
from orchestrator.errors import EntityAmbiguousError, EntityNotFoundError
from orchestrator.nodes.resolve_entity import make_resolve_entity_node
from tests.mocks.openai import (
    MockOpenAIClient,
    make_no_tool_call_response,
    make_tool_call_response,
)
from tests.mocks.postgres import MockPostgresConnection


def _graph_schema() -> GraphSchema:
    return GraphSchema.model_validate(
        {
            "nodes": {
                "Product": {
                    "uniqueKey": "productId",
                    "source": {"schema": "production", "table": "product"},
                    "properties": {
                        "productId": {"type": "INTEGER", "sourceColumn": "productid"},
                        "name": {"type": "STRING", "sourceColumn": "name"},
                    },
                },
                "Supplier": {
                    "uniqueKey": "supplierId",
                    "source": {"schema": "purchasing", "table": "vendor"},
                    "properties": {
                        "supplierId": {
                            "type": "INTEGER",
                            "sourceColumn": "businessentityid",
                        },
                        "name": {"type": "STRING", "sourceColumn": "name"},
                    },
                },
            },
            "relationships": {},
        }
    )


def test_make_resolve_entity_node_raises_when_no_named_entity_types() -> None:
    """이름 검색 가능한 엔티티 타입이 없는 스키마면 즉시 실패한다."""
    schema = GraphSchema.model_validate(
        {
            "nodes": {
                "WorkOrder": {
                    "uniqueKey": "workOrderId",
                    "source": {"schema": "production", "table": "workorder"},
                    "properties": {
                        "workOrderId": {
                            "type": "INTEGER",
                            "sourceColumn": "workorderid",
                        },
                    },
                },
            },
            "relationships": {},
        }
    )

    with pytest.raises(ValueError):
        make_resolve_entity_node(
            MockOpenAIClient(make_no_tool_call_response()),
            MockPostgresConnection(rows_by_name={}),
            schema,
        )


def test_resolve_entity_returns_entity_when_product_found() -> None:
    """질의에서 추출한 제품명이 DB에 있으면 productId를 확정한다."""
    openai_client = MockOpenAIClient(
        make_tool_call_response(
            "extract_entity",
            {"entityType": "product", "entityName": "Touring-1000 Yellow, 54"},
        )
    )
    postgres_connection = MockPostgresConnection(
        rows_by_name={"Touring-1000 Yellow, 54": (956, "Touring-1000 Yellow, 54")}
    )
    node = make_resolve_entity_node(openai_client, postgres_connection, _graph_schema())

    result = node({"query": "Touring-1000 Yellow, 54의 정가와 표준원가를 알려줘."})

    assert result == {
        "entity": {"productId": 956, "productName": "Touring-1000 Yellow, 54"}
    }
    assert openai_client.calls[0]["reasoning_effort"] == "none"


def test_resolve_entity_returns_entity_when_supplier_found() -> None:
    """질의에서 추출한 업체명이 DB에 있으면 supplierId를 확정한다."""
    openai_client = MockOpenAIClient(
        make_tool_call_response(
            "extract_entity",
            {"entityType": "supplier", "entityName": "Allenson Cycles"},
        )
    )
    postgres_connection = MockPostgresConnection(
        rows_by_name={"Allenson Cycles": (1494, "Allenson Cycles")}
    )
    node = make_resolve_entity_node(openai_client, postgres_connection, _graph_schema())

    result = node({"query": "공급업체 Allenson Cycles가 공급하는 부품을 알려줘."})

    assert result == {"entity": {"supplierId": 1494, "supplierName": "Allenson Cycles"}}


def test_resolve_entity_returns_none_entity_when_no_entity_mentioned() -> None:
    """특정 대상을 지칭하지 않는 질의는 DB 조회 없이 entity=None으로 통과한다."""
    openai_client = MockOpenAIClient(make_no_tool_call_response())
    postgres_connection = MockPostgresConnection(rows_by_name={})
    node = make_resolve_entity_node(openai_client, postgres_connection, _graph_schema())

    result = node({"query": "현재 활성 상태인 공급업체 수를 알려줘."})

    assert result == {"entity": None}
    assert postgres_connection.last_query is None


def test_resolve_entity_raises_when_entity_not_found_and_no_similar_names() -> None:
    """추출된 이름이 DB에 없고 유사한 이름도 없으면 EntityNotFoundError를 발생시킨다."""
    openai_client = MockOpenAIClient(
        make_tool_call_response(
            "extract_entity",
            {"entityType": "product", "entityName": "존재하지 않는 제품"},
        )
    )
    postgres_connection = MockPostgresConnection(rows_by_name={})
    node = make_resolve_entity_node(openai_client, postgres_connection, _graph_schema())

    with pytest.raises(EntityNotFoundError):
        node({"query": "존재하지 않는 제품의 정가를 알려줘."})


def test_resolve_entity_requires_openai_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """OPENAI_MODEL이 없으면 추출 요청 전에 즉시 실패한다."""
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    openai_client = MockOpenAIClient(make_no_tool_call_response())
    node = make_resolve_entity_node(
        openai_client,
        MockPostgresConnection(rows_by_name={}),
        _graph_schema(),
    )

    with pytest.raises(KeyError, match="OPENAI_MODEL"):
        node({"query": "제품의 정가를 알려줘."})

    assert openai_client.calls == []


def test_resolve_entity_returns_confirmed_entity_without_matching() -> None:
    """confirmed_entity가 있으면 매칭 없이 그대로 확정한다."""
    openai_client = MockOpenAIClient(make_no_tool_call_response())
    postgres_connection = MockPostgresConnection(rows_by_name={})
    node = make_resolve_entity_node(openai_client, postgres_connection, _graph_schema())

    result = node(
        {
            "query": "그 제품 정가 알려줘.",
            "confirmed_entity": {
                "productId": 956,
                "productName": "Touring-1000 Yellow, 54",
            },
        }
    )

    assert result == {
        "entity": {"productId": 956, "productName": "Touring-1000 Yellow, 54"}
    }
    assert openai_client.calls == []
    assert postgres_connection.last_query is None


def test_resolve_entity_raises_ambiguous_with_similar_candidates() -> None:
    """정확 일치가 없고 유사한 이름이 있으면 EntityAmbiguousError로 후보를 제시한다."""
    openai_client = MockOpenAIClient(
        make_tool_call_response(
            "extract_entity",
            {"entityType": "product", "entityName": "터치링 자전거"},
        )
    )
    postgres_connection = MockPostgresConnection(
        rows_by_name={},
        similar_rows_by_name={
            "터치링 자전거": [
                (956, "Touring-1000 Yellow, 54", 0.62),
                (957, "Touring-2000 Blue, 60", 0.41),
            ]
        },
    )
    node = make_resolve_entity_node(openai_client, postgres_connection, _graph_schema())

    with pytest.raises(EntityAmbiguousError) as excinfo:
        node({"query": "터치링 자전거 정가 알려줘."})

    assert excinfo.value.candidates == [
        {
            "id": 956,
            "name": "Touring-1000 Yellow, 54",
            "entityType": "product",
            "score": 0.62,
            "entity": {"productId": 956, "productName": "Touring-1000 Yellow, 54"},
        },
        {
            "id": 957,
            "name": "Touring-2000 Blue, 60",
            "entityType": "product",
            "score": 0.41,
            "entity": {"productId": 957, "productName": "Touring-2000 Blue, 60"},
        },
    ]


def test_resolve_entity_candidate_entity_round_trips_as_confirmed_entity() -> None:
    """candidates[0]["entity"]를 confirmed_entity로 재진입하면 매칭 없이 확정된다."""
    openai_client = MockOpenAIClient(
        make_tool_call_response(
            "extract_entity",
            {"entityType": "product", "entityName": "터치링 자전거"},
        )
    )
    postgres_connection = MockPostgresConnection(
        rows_by_name={},
        similar_rows_by_name={
            "터치링 자전거": [
                (956, "Touring-1000 Yellow, 54", 0.62),
            ]
        },
    )
    node = make_resolve_entity_node(openai_client, postgres_connection, _graph_schema())

    with pytest.raises(EntityAmbiguousError) as excinfo:
        node({"query": "터치링 자전거 정가 알려줘."})

    candidate_entity = excinfo.value.candidates[0]["entity"]
    assert candidate_entity == {
        "productId": 956,
        "productName": "Touring-1000 Yellow, 54",
    }

    reentry_openai_client = MockOpenAIClient(make_no_tool_call_response())
    reentry_postgres_connection = MockPostgresConnection(rows_by_name={})
    reentry_node = make_resolve_entity_node(
        reentry_openai_client, reentry_postgres_connection, _graph_schema()
    )

    result = reentry_node(
        {"query": "그 제품 정가 알려줘.", "confirmed_entity": candidate_entity}
    )

    assert result == {"entity": candidate_entity}
    assert reentry_openai_client.calls == []
    assert reentry_postgres_connection.last_query is None


def test_resolve_entity_falls_through_when_confirmed_entity_has_wrong_keys() -> None:
    """confirmed_entity 키가 알려진 엔티티 타입과 다르면 무시하고 재추출한다."""
    openai_client = MockOpenAIClient(
        make_tool_call_response(
            "extract_entity",
            {"entityType": "product", "entityName": "Touring-1000 Yellow, 54"},
        )
    )
    postgres_connection = MockPostgresConnection(
        rows_by_name={"Touring-1000 Yellow, 54": (956, "Touring-1000 Yellow, 54")}
    )
    node = make_resolve_entity_node(openai_client, postgres_connection, _graph_schema())

    result = node(
        {
            "query": "Touring-1000 Yellow, 54의 정가를 알려줘.",
            "confirmed_entity": {"productId": 956, "wrongKey": "x"},
        }
    )

    assert result == {
        "entity": {"productId": 956, "productName": "Touring-1000 Yellow, 54"}
    }
    assert len(openai_client.calls) == 1


def test_resolve_entity_falls_through_when_confirmed_entity_has_wrong_types() -> None:
    """confirmed_entity 값 타입이 다르면 무시하고 재추출한다."""
    openai_client = MockOpenAIClient(
        make_tool_call_response(
            "extract_entity",
            {"entityType": "product", "entityName": "Touring-1000 Yellow, 54"},
        )
    )
    postgres_connection = MockPostgresConnection(
        rows_by_name={"Touring-1000 Yellow, 54": (956, "Touring-1000 Yellow, 54")}
    )
    node = make_resolve_entity_node(openai_client, postgres_connection, _graph_schema())

    result = node(
        {
            "query": "Touring-1000 Yellow, 54의 정가를 알려줘.",
            "confirmed_entity": {
                "productId": "956",
                "productName": "Touring-1000 Yellow, 54",
            },
        }
    )

    assert result == {
        "entity": {"productId": 956, "productName": "Touring-1000 Yellow, 54"}
    }
    assert len(openai_client.calls) == 1


def test_resolve_entity_falls_through_when_confirmed_entity_is_unknown_type_combination() -> (
    None
):
    """confirmed_entity가 어떤 알려진 엔티티 타입의 키 조합과도 일치하지 않으면 무시한다."""
    openai_client = MockOpenAIClient(
        make_tool_call_response(
            "extract_entity",
            {"entityType": "product", "entityName": "Touring-1000 Yellow, 54"},
        )
    )
    postgres_connection = MockPostgresConnection(
        rows_by_name={"Touring-1000 Yellow, 54": (956, "Touring-1000 Yellow, 54")}
    )
    node = make_resolve_entity_node(openai_client, postgres_connection, _graph_schema())

    result = node(
        {
            "query": "Touring-1000 Yellow, 54의 정가를 알려줘.",
            "confirmed_entity": {"foo": 1, "bar": "x"},
        }
    )

    assert result == {
        "entity": {"productId": 956, "productName": "Touring-1000 Yellow, 54"}
    }
    assert len(openai_client.calls) == 1


def test_resolve_entity_returns_none_entity_when_llm_extracts_unknown_entity_type() -> (
    None
):
    """LLM이 알 수 없는 entityType을 반환하면 entity=None으로 처리한다."""
    openai_client = MockOpenAIClient(
        make_tool_call_response(
            "extract_entity",
            {"entityType": "category", "entityName": "Bikes"},
        )
    )
    postgres_connection = MockPostgresConnection(rows_by_name={})
    node = make_resolve_entity_node(openai_client, postgres_connection, _graph_schema())

    result = node({"query": "카테고리 Bikes에 속한 제품을 알려줘."})

    assert result == {"entity": None}
    assert postgres_connection.last_query is None


def test_resolve_entity_returns_none_entity_when_tool_call_arguments_missing_key() -> (
    None
):
    """tool call 인자에 필수 키가 없으면 entity=None으로 처리한다."""
    openai_client = MockOpenAIClient(
        make_tool_call_response("extract_entity", {"entityType": "product"})
    )
    postgres_connection = MockPostgresConnection(rows_by_name={})
    node = make_resolve_entity_node(openai_client, postgres_connection, _graph_schema())

    result = node({"query": "그 제품 정가 알려줘."})

    assert result == {"entity": None}
    assert postgres_connection.last_query is None
