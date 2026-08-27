"""resolve_entity 노드가 엔티티를 확정하거나 통과시키는 동작을 테스트한다."""

import psycopg
import pytest

from agents.cypher.schema.models import GraphSchema
from orchestrator.errors import EntityAmbiguousError, EntityNotFoundError
from orchestrator.nodes.resolve_entity import make_resolve_entity_node
from tests.mocks.openai import (
    MockOpenAIClient,
    make_no_tool_call_response,
    make_tool_call_response,
    make_tool_calls_response,
)
from tests.mocks.postgres import MockAsyncPostgresPool


def _graph_schema() -> GraphSchema:
    return GraphSchema.model_validate(
        {
            "nodes": {
                "Product": {
                    "uniqueKey": "productId",
                    "source": {"schema": "production", "table": "product"},
                    "aliases": ["제품", "부품", "완제품"],
                    "properties": {
                        "productId": {"type": "INTEGER", "sourceColumn": "productid"},
                        "name": {"type": "STRING", "sourceColumn": "name"},
                    },
                },
                "Supplier": {
                    "uniqueKey": "supplierId",
                    "source": {"schema": "purchasing", "table": "vendor"},
                    "aliases": ["공급업체", "업체", "공급사"],
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


async def test_make_resolve_entity_node_uses_sql_type_without_named_graph_type() -> (
    None
):
    """이름 검색 가능한 그래프 노드가 없어도 SQL 엔티티 타입을 사용한다."""
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

    openai_client = MockOpenAIClient(make_no_tool_call_response())
    node = make_resolve_entity_node(
        openai_client,
        MockAsyncPostgresPool(rows_by_name={}),
        schema,
    )

    assert await node({"query": "전체 제품 수를 알려줘."}) == {"entity": None}
    entity_type = openai_client.calls[0]["tools"][0]["function"]["parameters"][
        "properties"
    ]["entityType"]
    assert entity_type["enum"] == ["productCategory"]


async def test_resolve_entity_returns_entity_when_product_found() -> None:
    """질의에서 추출한 제품명이 DB에 있으면 productId를 확정한다."""
    openai_client = MockOpenAIClient(
        make_tool_call_response(
            "extract_entity",
            {"entityType": "product", "entityName": "Touring-1000 Yellow, 54"},
        )
    )
    pool = MockAsyncPostgresPool(
        rows_by_name={"Touring-1000 Yellow, 54": (956, "Touring-1000 Yellow, 54")}
    )
    node = make_resolve_entity_node(openai_client, pool, _graph_schema())

    result = await node(
        {"query": "Touring-1000 Yellow, 54의 정가와 표준원가를 알려줘."}
    )

    assert result == {
        "entity": {"productId": 956, "productName": "Touring-1000 Yellow, 54"}
    }
    assert openai_client.calls[0]["reasoning_effort"] == "none"
    entity_type = openai_client.calls[0]["tools"][0]["function"]["parameters"][
        "properties"
    ]["entityType"]
    assert "productCategory" in entity_type["enum"]
    assert "productCategory: 제품 분류" in entity_type["description"]


async def test_resolve_entity_returns_entity_when_product_category_found() -> None:
    """질의에서 추출한 제품 분류명을 productCategoryId로 확정한다."""
    openai_client = MockOpenAIClient(
        make_tool_call_response(
            "extract_entity",
            {"entityType": "productCategory", "entityName": "Components"},
        )
    )
    pool = MockAsyncPostgresPool(rows_by_name={"Components": (2, "Components")})
    node = make_resolve_entity_node(openai_client, pool, _graph_schema())

    result = await node({"query": "Components에 포함된 제품 수를 알려줘."})

    assert result == {
        "entity": {
            "productCategoryId": 2,
            "productCategoryName": "Components",
        }
    }
    assert pool.last_query == (
        "SELECT productcategoryid, name "
        "FROM production.productcategory WHERE name = %s",
        ("Components",),
    )


async def test_resolve_entity_returns_entity_when_supplier_found() -> None:
    """질의에서 추출한 업체명이 DB에 있으면 supplierId를 확정한다."""
    openai_client = MockOpenAIClient(
        make_tool_call_response(
            "extract_entity",
            {"entityType": "supplier", "entityName": "Allenson Cycles"},
        )
    )
    pool = MockAsyncPostgresPool(
        rows_by_name={"Allenson Cycles": (1494, "Allenson Cycles")}
    )
    node = make_resolve_entity_node(openai_client, pool, _graph_schema())

    result = await node({"query": "공급업체 Allenson Cycles가 공급하는 부품을 알려줘."})

    assert result == {"entity": {"supplierId": 1494, "supplierName": "Allenson Cycles"}}


async def test_resolve_entity_returns_all_named_entities_in_question_order() -> None:
    """두 제품을 비교하는 질문은 어느 하나를 버리지 않고 순서대로 확정한다."""
    openai_client = MockOpenAIClient(
        make_tool_calls_response(
            [
                (
                    "extract_entity",
                    {"entityType": "product", "entityName": "Road-650 Black, 58"},
                ),
                (
                    "extract_entity",
                    {
                        "entityType": "product",
                        "entityName": "Mountain-100 Black, 38",
                    },
                ),
            ]
        )
    )
    pool = MockAsyncPostgresPool(
        rows_by_name={
            "Road-650 Black, 58": (765, "Road-650 Black, 58"),
            "Mountain-100 Black, 38": (775, "Mountain-100 Black, 38"),
        }
    )
    node = make_resolve_entity_node(openai_client, pool, _graph_schema())

    result = await node(
        {"query": ("Road-650 Black, 58과 Mountain-100 Black, 38의 공통 부품을 알려줘.")}
    )

    assert result == {
        "entity": [
            {"productId": 765, "productName": "Road-650 Black, 58"},
            {"productId": 775, "productName": "Mountain-100 Black, 38"},
        ]
    }


async def test_resolve_entity_returns_none_entity_when_no_entity_mentioned() -> None:
    """특정 대상을 지칭하지 않는 질의는 DB 조회 없이 entity=None으로 통과한다."""
    openai_client = MockOpenAIClient(make_no_tool_call_response())
    pool = MockAsyncPostgresPool(rows_by_name={})
    node = make_resolve_entity_node(openai_client, pool, _graph_schema())

    result = await node({"query": "현재 활성 상태인 공급업체 수를 알려줘."})

    assert result == {"entity": None}
    assert pool.last_query is None


async def test_resolve_entity_ignores_entity_type_alias_as_name() -> None:
    """종류를 나타내는 표현 자체는 이름으로 조회하지 않는다."""
    openai_client = MockOpenAIClient(
        make_tool_call_response(
            "extract_entity",
            {"entityType": "productCategory", "entityName": "제품"},
        )
    )
    pool = MockAsyncPostgresPool(rows_by_name={})
    node = make_resolve_entity_node(openai_client, pool, _graph_schema())

    result = await node({"query": "판매 종료일이 없는 제품을 보여줘."})

    assert result == {"entity": None}
    assert pool.last_query is None


async def test_resolve_entity_raises_when_entity_not_found_and_no_similar_names() -> (
    None
):
    """추출된 이름이 DB에 없고 유사한 이름도 없으면 EntityNotFoundError를 발생시킨다."""
    openai_client = MockOpenAIClient(
        make_tool_call_response(
            "extract_entity",
            {"entityType": "product", "entityName": "존재하지 않는 제품"},
        )
    )
    pool = MockAsyncPostgresPool(rows_by_name={})
    node = make_resolve_entity_node(openai_client, pool, _graph_schema())

    with pytest.raises(EntityNotFoundError):
        await node({"query": "존재하지 않는 제품의 정가를 알려줘."})


async def test_resolve_entity_requires_openai_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """OPENAI_MODEL이 없으면 추출 요청 전에 즉시 실패한다."""
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    openai_client = MockOpenAIClient(make_no_tool_call_response())
    node = make_resolve_entity_node(
        openai_client,
        MockAsyncPostgresPool(rows_by_name={}),
        _graph_schema(),
    )

    with pytest.raises(KeyError, match="OPENAI_MODEL"):
        await node({"query": "제품의 정가를 알려줘."})

    assert openai_client.calls == []


async def test_resolve_entity_returns_confirmed_entity_when_it_matches_db() -> None:
    """confirmed_entity가 유효하고 추가 이름이 없으면 그대로 유지한다."""
    openai_client = MockOpenAIClient(make_no_tool_call_response())
    pool = MockAsyncPostgresPool(
        rows_by_name={"Touring-1000 Yellow, 54": (956, "Touring-1000 Yellow, 54")}
    )
    node = make_resolve_entity_node(openai_client, pool, _graph_schema())

    result = await node(
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
    assert len(openai_client.calls) == 1


async def test_resolve_entity_falls_through_when_confirmed_entity_not_in_db() -> None:
    """confirmed_entity 형태는 맞지만 DB에 없는 값이면 무시하고 재추출한다."""
    openai_client = MockOpenAIClient(
        make_tool_call_response(
            "extract_entity",
            {"entityType": "product", "entityName": "Touring-1000 Yellow, 54"},
        )
    )
    pool = MockAsyncPostgresPool(
        rows_by_name={"Touring-1000 Yellow, 54": (956, "Touring-1000 Yellow, 54")}
    )
    node = make_resolve_entity_node(openai_client, pool, _graph_schema())

    result = await node(
        {
            "query": "Touring-1000 Yellow, 54의 정가를 알려줘.",
            "confirmed_entity": {
                "productId": 999999,
                "productName": "존재하지 않는 제품",
            },
        }
    )

    assert result == {
        "entity": {"productId": 956, "productName": "Touring-1000 Yellow, 54"}
    }
    assert len(openai_client.calls) == 1


async def test_resolve_entity_raises_not_found_when_pg_trgm_unavailable() -> None:
    """pg_trgm을 쓸 수 없으면 후보 없음으로 처리하고 EntityNotFoundError를 발생시킨다."""
    openai_client = MockOpenAIClient(
        make_tool_call_response(
            "extract_entity",
            {"entityType": "product", "entityName": "존재하지 않는 제품"},
        )
    )
    pool = MockAsyncPostgresPool(
        rows_by_name={},
        similarity_error=psycopg.errors.UndefinedFunction(
            "function similarity(text, text) does not exist"
        ),
    )
    node = make_resolve_entity_node(openai_client, pool, _graph_schema())

    with pytest.raises(EntityNotFoundError):
        await node({"query": "존재하지 않는 제품의 정가를 알려줘."})

    assert pool.rollback_called


async def test_resolve_entity_propagates_non_pg_trgm_database_errors() -> None:
    """pg_trgm 미설치가 아닌 일반 DB 예외는 삼키지 않고 그대로 전파한다."""
    openai_client = MockOpenAIClient(
        make_tool_call_response(
            "extract_entity",
            {"entityType": "product", "entityName": "존재하지 않는 제품"},
        )
    )
    pool = MockAsyncPostgresPool(
        rows_by_name={},
        similarity_error=psycopg.OperationalError("connection lost"),
    )
    node = make_resolve_entity_node(openai_client, pool, _graph_schema())

    with pytest.raises(psycopg.OperationalError):
        await node({"query": "존재하지 않는 제품의 정가를 알려줘."})

    assert not pool.rollback_called


async def test_resolve_entity_raises_ambiguous_with_similar_candidates() -> None:
    """정확 일치가 없고 유사한 이름이 있으면 EntityAmbiguousError로 후보를 제시한다."""
    openai_client = MockOpenAIClient(
        make_tool_call_response(
            "extract_entity",
            {"entityType": "product", "entityName": "터치링 자전거"},
        )
    )
    pool = MockAsyncPostgresPool(
        rows_by_name={},
        similar_rows_by_name={
            "터치링 자전거": [
                (956, "Touring-1000 Yellow, 54", 0.62),
                (957, "Touring-2000 Blue, 60", 0.41),
            ]
        },
    )
    node = make_resolve_entity_node(openai_client, pool, _graph_schema())

    with pytest.raises(EntityAmbiguousError) as excinfo:
        await node({"query": "터치링 자전거 정가 알려줘."})

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


async def test_resolve_entity_candidate_entity_round_trips_as_confirmed_entity() -> (
    None
):
    """후보 응답의 entity를 재확인 요청에 그대로 사용할 수 있다."""
    openai_client = MockOpenAIClient(
        make_tool_call_response(
            "extract_entity",
            {"entityType": "product", "entityName": "터치링 자전거"},
        )
    )
    pool = MockAsyncPostgresPool(
        rows_by_name={},
        similar_rows_by_name={
            "터치링 자전거": [
                (956, "Touring-1000 Yellow, 54", 0.62),
            ]
        },
    )
    node = make_resolve_entity_node(openai_client, pool, _graph_schema())

    with pytest.raises(EntityAmbiguousError) as excinfo:
        await node({"query": "터치링 자전거 정가 알려줘."})

    candidate_entity = excinfo.value.candidates[0]["entity"]
    assert candidate_entity == {
        "productId": 956,
        "productName": "Touring-1000 Yellow, 54",
    }

    reentry_openai_client = MockOpenAIClient(make_no_tool_call_response())
    reentry_pool = MockAsyncPostgresPool(
        rows_by_name={"Touring-1000 Yellow, 54": (956, "Touring-1000 Yellow, 54")}
    )
    reentry_node = make_resolve_entity_node(
        reentry_openai_client, reentry_pool, _graph_schema()
    )

    result = await reentry_node(
        {"query": "그 제품 정가 알려줘.", "confirmed_entity": candidate_entity}
    )

    assert result == {"entity": candidate_entity}
    assert len(reentry_openai_client.calls) == 1


async def test_resolve_entity_merges_confirmed_candidate_with_other_named_entity() -> (
    None
):
    """confirmed_entity와 새로 추출된 다른 엔티티를 함께 확정한다."""
    openai_client = MockOpenAIClient(
        make_tool_calls_response(
            [
                (
                    "extract_entity",
                    {"entityType": "product", "entityName": "터치링 자전거"},
                ),
                (
                    "extract_entity",
                    {
                        "entityType": "product",
                        "entityName": "Mountain-100 Black, 38",
                    },
                ),
            ]
        )
    )
    confirmed_entity = {
        "productId": 956,
        "productName": "Touring-1000 Yellow, 54",
    }
    pool = MockAsyncPostgresPool(
        rows_by_name={
            "Touring-1000 Yellow, 54": (956, "Touring-1000 Yellow, 54"),
            "Mountain-100 Black, 38": (775, "Mountain-100 Black, 38"),
        },
        similar_rows_by_name={
            "터치링 자전거": [(956, "Touring-1000 Yellow, 54", 0.62)]
        },
    )
    node = make_resolve_entity_node(openai_client, pool, _graph_schema())

    result = await node(
        {
            "query": "터치링 자전거와 Mountain-100 Black, 38의 공통 부품을 알려줘.",
            "confirmed_entity": confirmed_entity,
        }
    )

    assert result == {
        "entity": [
            confirmed_entity,
            {"productId": 775, "productName": "Mountain-100 Black, 38"},
        ]
    }


async def test_resolve_entity_falls_through_when_confirmed_entity_has_wrong_keys() -> (
    None
):
    """confirmed_entity 키가 알려진 엔티티 타입과 다르면 무시하고 재추출한다."""
    openai_client = MockOpenAIClient(
        make_tool_call_response(
            "extract_entity",
            {"entityType": "product", "entityName": "Touring-1000 Yellow, 54"},
        )
    )
    pool = MockAsyncPostgresPool(
        rows_by_name={"Touring-1000 Yellow, 54": (956, "Touring-1000 Yellow, 54")}
    )
    node = make_resolve_entity_node(openai_client, pool, _graph_schema())

    result = await node(
        {
            "query": "Touring-1000 Yellow, 54의 정가를 알려줘.",
            "confirmed_entity": {"productId": 956, "wrongKey": "x"},
        }
    )

    assert result == {
        "entity": {"productId": 956, "productName": "Touring-1000 Yellow, 54"}
    }
    assert len(openai_client.calls) == 1


async def test_resolve_entity_falls_through_when_confirmed_entity_has_wrong_types() -> (
    None
):
    """confirmed_entity 값 타입이 다르면 무시하고 재추출한다."""
    openai_client = MockOpenAIClient(
        make_tool_call_response(
            "extract_entity",
            {"entityType": "product", "entityName": "Touring-1000 Yellow, 54"},
        )
    )
    pool = MockAsyncPostgresPool(
        rows_by_name={"Touring-1000 Yellow, 54": (956, "Touring-1000 Yellow, 54")}
    )
    node = make_resolve_entity_node(openai_client, pool, _graph_schema())

    result = await node(
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


async def test_resolve_entity_falls_through_when_confirmed_entity_is_unknown_type_combination() -> (
    None
):
    """confirmed_entity가 어떤 알려진 엔티티 타입의 키 조합과도 일치하지 않으면 무시한다."""
    openai_client = MockOpenAIClient(
        make_tool_call_response(
            "extract_entity",
            {"entityType": "product", "entityName": "Touring-1000 Yellow, 54"},
        )
    )
    pool = MockAsyncPostgresPool(
        rows_by_name={"Touring-1000 Yellow, 54": (956, "Touring-1000 Yellow, 54")}
    )
    node = make_resolve_entity_node(openai_client, pool, _graph_schema())

    result = await node(
        {
            "query": "Touring-1000 Yellow, 54의 정가를 알려줘.",
            "confirmed_entity": {"foo": 1, "bar": "x"},
        }
    )

    assert result == {
        "entity": {"productId": 956, "productName": "Touring-1000 Yellow, 54"}
    }
    assert len(openai_client.calls) == 1


async def test_resolve_entity_returns_none_entity_when_llm_extracts_unknown_entity_type() -> (
    None
):
    """LLM이 알 수 없는 entityType을 반환하면 entity=None으로 처리한다."""
    openai_client = MockOpenAIClient(
        make_tool_call_response(
            "extract_entity",
            {"entityType": "category", "entityName": "Bikes"},
        )
    )
    pool = MockAsyncPostgresPool(rows_by_name={})
    node = make_resolve_entity_node(openai_client, pool, _graph_schema())

    result = await node({"query": "카테고리 Bikes에 속한 제품을 알려줘."})

    assert result == {"entity": None}
    assert pool.last_query is None


@pytest.mark.parametrize(
    "arguments",
    [
        {"entityType": "product"},
        {"entityType": "product", "entityName": 123},
    ],
    ids=["missing-key", "wrong-type"],
)
async def test_resolve_entity_returns_none_entity_for_invalid_tool_arguments(
    arguments: dict[str, object],
) -> None:
    """tool call 인자의 키나 타입이 잘못되면 entity=None으로 처리한다."""
    openai_client = MockOpenAIClient(
        make_tool_call_response("extract_entity", arguments)
    )
    pool = MockAsyncPostgresPool(rows_by_name={})
    node = make_resolve_entity_node(openai_client, pool, _graph_schema())

    result = await node({"query": "그 제품 정가 알려줘."})

    assert result == {"entity": None}
    assert pool.last_query is None


async def test_resolve_entity_resolves_multiple_ambiguous_entities_via_confirmed_list() -> (
    None
):
    """confirmed_entity가 리스트면 모호한 이름이 여러 개여도 전부 확정된 값으로 매칭한다."""
    openai_client = MockOpenAIClient(
        make_tool_calls_response(
            [
                (
                    "extract_entity",
                    {"entityType": "product", "entityName": "터치링 자전거"},
                ),
                (
                    "extract_entity",
                    {"entityType": "product", "entityName": "마운틴 자전거"},
                ),
            ]
        )
    )
    pool = MockAsyncPostgresPool(
        rows_by_name={
            "Touring-1000 Yellow, 54": (956, "Touring-1000 Yellow, 54"),
            "Mountain-100 Black, 38": (775, "Mountain-100 Black, 38"),
        },
        similar_rows_by_name={
            "터치링 자전거": [
                (956, "Touring-1000 Yellow, 54", 0.62),
                (957, "Touring-2000 Blue, 60", 0.41),
            ],
            "마운틴 자전거": [
                (775, "Mountain-100 Black, 38", 0.58),
                (776, "Mountain-200 Silver, 42", 0.45),
            ],
        },
    )
    node = make_resolve_entity_node(openai_client, pool, _graph_schema())

    confirmed_entities = [
        {"productId": 956, "productName": "Touring-1000 Yellow, 54"},
        {"productId": 775, "productName": "Mountain-100 Black, 38"},
    ]

    result = await node(
        {
            "query": "터치링 자전거와 마운틴 자전거의 공통 부품을 알려줘.",
            "confirmed_entity": confirmed_entities,
        }
    )

    assert result == {"entity": confirmed_entities}
