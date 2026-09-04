"""명시적 엔티티 추출과 DB 조회 계약을 테스트한다."""

import psycopg
import pytest

from agents.cypher.schema.models import GraphSchema
from orchestrator.entity_types import list_resolvable_entity_types
from orchestrator.errors import EntityAmbiguousError, EntityNotFoundError
from orchestrator.nodes.resolve_entity import (
    EntityExtractionError,
    EntityResolutionSettings,
    _build_extract_entity_tool,
    _literal_name_candidates,
    load_entity_resolution_settings,
    make_resolve_entity_node,
)
from tests.mocks.openai import (
    MockOpenAIClient,
    make_no_tool_call_response,
    make_tool_call_response,
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
                        "productId": {
                            "type": "INTEGER",
                            "sourceColumn": "productid",
                        },
                        "name": {"type": "STRING", "sourceColumn": "name"},
                    },
                },
                "Supplier": {
                    "uniqueKey": "supplierId",
                    "source": {"schema": "purchasing", "table": "vendor"},
                    "aliases": ["공급업체", "공급사"],
                    "properties": {
                        "supplierId": {
                            "type": "INTEGER",
                            "sourceColumn": "businessentityid",
                        },
                        "name": {"type": "STRING", "sourceColumn": "name"},
                    },
                },
                "Location": {
                    "uniqueKey": "locationId",
                    "source": {"schema": "production", "table": "location"},
                    "aliases": ["작업장"],
                    "properties": {
                        "locationId": {
                            "type": "INTEGER",
                            "sourceColumn": "locationid",
                        },
                        "name": {"type": "STRING", "sourceColumn": "name"},
                    },
                },
                "ScrapReason": {
                    "uniqueKey": "scrapReasonId",
                    "source": {"schema": "production", "table": "scrapreason"},
                    "aliases": ["폐기 사유", "폐기사유", "폐기 이유", "폐기이유"],
                    "properties": {
                        "scrapReasonId": {
                            "type": "INTEGER",
                            "sourceColumn": "scrapreasonid",
                        },
                        "name": {"type": "STRING", "sourceColumn": "name"},
                    },
                },
            },
            "relationships": {},
        }
    )


def _node(client: MockOpenAIClient, pool: MockAsyncPostgresPool):
    return make_resolve_entity_node(
        client,
        pool,
        _graph_schema(),
        settings=EntityResolutionSettings(0.42, 7),
    )


def _entity_response(*entities: dict[str, str]):
    return make_tool_call_response("extract_entities", {"entities": list(entities)})


def test_entity_tool_collects_all_mentions_in_one_ordered_array() -> None:
    tool = _build_extract_entity_tool(list_resolvable_entity_types(_graph_schema()))
    function = tool["function"]
    entities = function["parameters"]["properties"]["entities"]

    assert function["name"] == "extract_entities"
    assert entities["type"] == "array"
    assert "minItems" not in entities
    assert entities["items"]["required"] == ["entityType", "entityName"]


async def test_empty_entity_array_without_literal_candidates_skips_database() -> None:
    pool = MockAsyncPostgresPool(rows_by_name={})
    client = MockOpenAIClient(_entity_response())

    result = await _node(client, pool)({"query": "제품 수"})

    assert result == {"entity": None}
    assert pool.queries == []
    assert "tool_choice" not in client.calls[0]
    assert "일반 단어나 복수형처럼 보이더라도" not in (
        client.calls[0]["messages"][0]["content"]
    )


async def test_no_tool_call_uses_only_candidate_equality_lookups() -> None:
    client = MockOpenAIClient(make_no_tool_call_response())
    pool = MockAsyncPostgresPool(rows_by_name={})

    result = await _node(client, pool)(
        {"query": "A Name과 Long A Name을 문장에 우연히 쓴다"}
    )

    assert result == {"entity": None}
    assert pool.queries
    assert all("= ANY(%s)" in query for query, _ in pool.queries)
    assert all("strpos(lower(" not in query for query, _ in pool.queries)


async def test_literal_database_name_merges_when_llm_extracts_nothing() -> None:
    client = MockOpenAIClient(_entity_response())
    pool = MockAsyncPostgresPool(
        rows_by_name={},
        rows_by_table_and_name={
            ("production.productcategory", "Components"): (3, "Components")
        },
    )

    result = await _node(client, pool)({"query": "Components 제품을 보여줘"})

    assert result == {
        "entity": {
            "productCategoryId": 3,
            "productCategoryName": "Components",
        }
    }
    assert len(client.calls) == 1
    assert all("strpos(lower(" not in query for query, _ in pool.queries)


async def test_literal_lookup_prefers_longest_overlapping_database_name() -> None:
    client = MockOpenAIClient(_entity_response())
    query = "Touring-1000 Yellow, 54의 재고를 보여줘"
    pool = MockAsyncPostgresPool(
        rows_by_name={},
        rows_by_table_and_name={
            ("production.product", "Touring-1000 Yellow"): (
                1,
                "Touring-1000 Yellow",
            ),
            ("production.product", "Touring-1000 Yellow, 54"): (
                2,
                "Touring-1000 Yellow, 54",
            ),
        },
    )

    result = await _node(client, pool)({"query": query})

    assert result == {
        "entity": {
            "productId": 2,
            "productName": "Touring-1000 Yellow, 54",
        }
    }
    assert len(client.calls) == 1


@pytest.mark.parametrize(
    "extracted_name",
    [
        "Touring-1000 Yellow",
        "touring-1000 yellow, 54",
        "Touring-1000 Yellow 54",
        "Touring-1000 Yellow,54",
        "Touring-1000 Yellow, 54",
    ],
)
async def test_exact_literal_suppresses_only_its_nested_extraction(
    extracted_name: str,
) -> None:
    query = "Touring-1000 Yellow, 54의 재고를 보여줘"
    client = MockOpenAIClient(
        _entity_response({"entityType": "product", "entityName": extracted_name})
    )
    pool = MockAsyncPostgresPool(
        rows_by_name={},
        rows_by_table_and_name={
            ("production.product", "Touring-1000 Yellow, 54"): (
                956,
                "Touring-1000 Yellow, 54",
            )
        },
        similar_rows_by_name={extracted_name: [(956, "Touring-1000 Yellow, 54", 0.9)]},
    )

    result = await _node(client, pool)({"query": query})

    assert result == {
        "entity": {
            "productId": 956,
            "productName": "Touring-1000 Yellow, 54",
        }
    }
    assert all("similarity(" not in sql for sql, _ in pool.queries)


async def test_nested_extraction_at_another_position_is_not_suppressed() -> None:
    query = "Touring-1000 Yellow와 Touring-1000 Yellow, 54의 재고를 비교해줘"
    client = MockOpenAIClient(
        _entity_response({"entityType": "product", "entityName": "Touring-1000 Yellow"})
    )
    pool = MockAsyncPostgresPool(
        rows_by_name={"Touring-1000 Yellow": (955, "Touring-1000 Yellow")},
        rows_by_table_and_name={
            ("production.product", "Touring-1000 Yellow, 54"): (
                956,
                "Touring-1000 Yellow, 54",
            )
        },
    )

    result = await _node(client, pool)({"query": query})

    assert result["entity"] == [
        {"productId": 955, "productName": "Touring-1000 Yellow"},
        {"productId": 956, "productName": "Touring-1000 Yellow, 54"},
    ]


async def test_nested_extraction_with_a_different_type_is_not_suppressed() -> None:
    query = "Touring-1000 Yellow, 54의 공급업체 정보를 보여줘"
    client = MockOpenAIClient(
        _entity_response(
            {"entityType": "supplier", "entityName": "Touring-1000 Yellow"}
        )
    )
    pool = MockAsyncPostgresPool(
        rows_by_name={},
        rows_by_table_and_name={
            ("production.product", "Touring-1000 Yellow, 54"): (
                956,
                "Touring-1000 Yellow, 54",
            )
        },
    )

    with pytest.raises(EntityNotFoundError, match="Touring-1000 Yellow"):
        await _node(client, pool)({"query": query})


async def test_digits_only_extraction_is_ignored_without_database_lookup() -> None:
    client = MockOpenAIClient(
        _entity_response({"entityType": "product", "entityName": "제품 54"})
    )
    pool = MockAsyncPostgresPool(rows_by_name={})

    assert await _node(client, pool)({"query": "제품 54"}) == {"entity": None}
    assert pool.queries == []


@pytest.mark.parametrize("alias", ["폐기 이유", "폐기이유", "폐 기 이 유"])
async def test_explicit_scrap_reason_type_alias_is_not_looked_up(alias: str) -> None:
    client = MockOpenAIClient(
        _entity_response({"entityType": "scrapReason", "entityName": alias})
    )
    pool = MockAsyncPostgresPool(rows_by_name={})

    assert await _node(client, pool)({"query": f"{alias}별 작업지시"}) == {
        "entity": None
    }
    assert pool.queries == []


async def test_literal_entities_keep_question_order_across_types() -> None:
    client = MockOpenAIClient(_entity_response())
    query = "North Foundry가 Cinder Bolt에 공급하는 항목"
    pool = MockAsyncPostgresPool(
        rows_by_name={},
        rows_by_table_and_name={
            ("production.product", "Cinder Bolt"): (11, "Cinder Bolt"),
            ("purchasing.vendor", "North Foundry"): (22, "North Foundry"),
        },
    )

    result = await _node(client, pool)({"query": query})

    assert result["entity"] == [
        {"supplierId": 22, "supplierName": "North Foundry"},
        {"productId": 11, "productName": "Cinder Bolt"},
    ]
    assert len(client.calls) == 1


async def test_literal_lookup_does_not_match_inside_ascii_word() -> None:
    query = "Components 제품을 보여줘"
    client = MockOpenAIClient(make_no_tool_call_response())
    pool = MockAsyncPostgresPool(
        rows_by_name={},
        rows_by_table_and_name={
            ("production.productcategory", "Component"): (3, "Component")
        },
    )

    assert await _node(client, pool)({"query": query}) == {"entity": None}
    assert len(client.calls) == 1


async def test_same_literal_in_multiple_types_defers_role_to_llm() -> None:
    query = "Shared Name 공급사를 보여줘"
    client = MockOpenAIClient(
        _entity_response({"entityType": "supplier", "entityName": "Shared Name"})
    )
    pool = MockAsyncPostgresPool(
        rows_by_name={},
        rows_by_table_and_name={
            ("production.product", "Shared Name"): (81, "Shared Name"),
            ("purchasing.vendor", "Shared Name"): (82, "Shared Name"),
        },
    )

    result = await _node(client, pool)({"query": query})

    assert result == {"entity": {"supplierId": 82, "supplierName": "Shared Name"}}
    assert len(client.calls) == 1


@pytest.mark.parametrize("name", ["제품", "product"])
async def test_exact_schema_type_alias_is_ignored_as_generic(name: str) -> None:
    client = MockOpenAIClient(
        _entity_response({"entityType": "product", "entityName": name})
    )
    pool = MockAsyncPostgresPool(rows_by_name={})

    assert await _node(client, pool)({"query": "제품 수"}) == {"entity": None}
    assert pool.queries == []


def test_literal_candidates_preserve_mixed_language_database_names() -> None:
    query = (
        "완제품 HL Road Frame - Black, 58과 부품 Metal Sheet 5가 "
        "Allenson Cycles에 연결돼 있어?"
    )

    assert _literal_name_candidates(query) == (
        "HL Road Frame - Black, 58",
        "Metal Sheet 5",
        "Allenson Cycles",
    )


async def test_whitespace_delimited_type_prefix_and_suffix_are_stripped() -> None:
    client = MockOpenAIClient(
        _entity_response(
            {"entityType": "product", "entityName": "제품 Cinder Bolt"},
            {"entityType": "supplier", "entityName": "North Foundry 공급사"},
        )
    )
    pool = MockAsyncPostgresPool(
        rows_by_name={
            "Cinder Bolt": (11, "Cinder Bolt"),
            "North Foundry": (22, "North Foundry"),
        }
    )

    result = await _node(client, pool)({"query": "두 이름"})

    assert result["entity"] == [
        {"productId": 11, "productName": "Cinder Bolt"},
        {"supplierId": 22, "supplierName": "North Foundry"},
    ]


async def test_two_exact_entities_keep_extraction_order_and_duplicates_are_removed() -> (
    None
):
    client = MockOpenAIClient(
        _entity_response(
            {"entityType": "product", "entityName": "Short Name"},
            {"entityType": "product", "entityName": "Short Name Extended"},
            {"entityType": "product", "entityName": "Short Name"},
        )
    )
    pool = MockAsyncPostgresPool(
        rows_by_name={
            "Short Name": (31, "Short Name"),
            "Short Name Extended": (32, "Short Name Extended"),
        }
    )

    result = await _node(client, pool)({"query": "overlapping names"})

    assert result["entity"] == [
        {"productId": 31, "productName": "Short Name"},
        {"productId": 32, "productName": "Short Name Extended"},
    ]


async def test_one_success_and_one_miss_fails_instead_of_partial_success() -> None:
    client = MockOpenAIClient(
        _entity_response(
            {"entityType": "product", "entityName": "Known"},
            {"entityType": "product", "entityName": "Absent"},
        )
    )
    pool = MockAsyncPostgresPool(rows_by_name={"Known": (41, "Known")})

    with pytest.raises(EntityNotFoundError) as exc_info:
        await _node(client, pool)({"query": "Known and Absent"})
    assert exc_info.value.entity_name == "Absent"


async def test_alias_fragments_are_explicit_claims_and_fail_lookup() -> None:
    client = MockOpenAIClient(
        _entity_response(
            {"entityType": "product", "entityName": "완제"},
            {"entityType": "supplier", "entityName": "공급"},
        )
    )

    with pytest.raises(EntityNotFoundError):
        await _node(client, MockAsyncPostgresPool(rows_by_name={}))(
            {"query": "two fragments"}
        )


async def test_success_and_similar_candidate_raises_ambiguous() -> None:
    client = MockOpenAIClient(
        _entity_response(
            {"entityType": "product", "entityName": "Known"},
            {"entityType": "supplier", "entityName": "Nort Foundry"},
        )
    )
    pool = MockAsyncPostgresPool(
        rows_by_name={"Known": (51, "Known")},
        similar_rows_by_name={"Nort Foundry": [(52, "North Foundry", 0.8)]},
    )

    with pytest.raises(EntityAmbiguousError) as exc_info:
        await _node(client, pool)({"query": "known and typo"})

    assert exc_info.value.candidates[0]["entity"] == {
        "supplierId": 52,
        "supplierName": "North Foundry",
    }
    assert pool.last_query is not None
    assert pool.last_query[1][-2:] == (0.42, 7)


async def test_literal_success_does_not_hide_extracted_typo() -> None:
    """정확한 literal 하나가 다른 명시적 오타 엔티티를 가리면 안 된다."""
    query = "Cinder Bolt와 Nort Foundry 공급업체를 비교해줘"
    client = MockOpenAIClient(
        _entity_response(
            {"entityType": "product", "entityName": "Cinder Bolt"},
            {"entityType": "supplier", "entityName": "Nort Foundry"},
        )
    )
    pool = MockAsyncPostgresPool(
        rows_by_name={},
        rows_by_table_and_name={
            ("production.product", "Cinder Bolt"): (91, "Cinder Bolt")
        },
        similar_rows_by_name={"Nort Foundry": [(92, "North Foundry", 0.8)]},
    )

    with pytest.raises(EntityAmbiguousError) as exc_info:
        await _node(client, pool)({"query": query})

    assert exc_info.value.lookup_name == "Nort Foundry"
    assert exc_info.value.candidates[0]["entity"] == {
        "supplierId": 92,
        "supplierName": "North Foundry",
    }
    assert any("= ANY(%s)" in sql for sql, _ in pool.queries)
    assert all("strpos(lower(" not in sql for sql, _ in pool.queries)


@pytest.mark.parametrize("similar", [False, True])
async def test_confirmed_entity_does_not_hide_another_explicit_failure(
    similar: bool,
) -> None:
    confirmed = {"productId": 61, "productName": "Confirmed"}
    client = MockOpenAIClient(
        _entity_response({"entityType": "supplier", "entityName": "Missing Foundry"})
    )
    pool = MockAsyncPostgresPool(
        rows_by_name={"Confirmed": (61, "Confirmed")},
        similar_rows_by_name=(
            {"Missing Foundry": [(62, "Similar Foundry", 0.7)]} if similar else {}
        ),
    )

    expected = EntityAmbiguousError if similar else EntityNotFoundError
    with pytest.raises(expected):
        await _node(client, pool)(
            {
                "query": "follow-up",
                "confirmed_entity": {"entity": confirmed, "forName": "Confirmed"},
            }
        )


async def test_confirmed_entity_is_verified_and_deduplicated_with_extraction() -> None:
    confirmed = {"productId": 71, "productName": "Confirmed"}
    client = MockOpenAIClient(
        _entity_response({"entityType": "product", "entityName": "Confirmed"})
    )
    pool = MockAsyncPostgresPool(rows_by_name={"Confirmed": (71, "Confirmed")})

    result = await _node(client, pool)(
        {
            "query": "Confirmed",
            "confirmed_entity": {"entity": confirmed, "forName": "Confirmed"},
        }
    )

    assert result == {"entity": confirmed}


async def test_confirmed_entity_resolves_when_same_ambiguous_wording_is_resent() -> (
    None
):
    """사용자가 후보를 선택한 뒤 client가 확정된 이름으로 질문을 다시 쓰지 않고
    원래 질문을 재전송하면 같은 모호한 표현이 다시 추출될 수 있다. 이때
    confirmed_entity와 그 엔티티가 답한 정확한 조회 텍스트(forName)를 연결하면,
    확정 행이 사용자에게 보인 작은 top-N에 계속 포함되는지와 무관하게
    EntityAmbiguousError를 다시 발생시키지 않고 해결할 수 있다."""
    confirmed = {"productId": 956, "productName": "Touring-1000 Yellow, 54"}
    client = MockOpenAIClient(
        _entity_response({"entityType": "product", "entityName": "터치링 자전거"})
    )
    pool = MockAsyncPostgresPool(
        rows_by_name={"Touring-1000 Yellow, 54": (956, "Touring-1000 Yellow, 54")},
        similar_rows_by_name={"터치링 자전거": [(956, "Touring-1000 Yellow, 54", 0.5)]},
    )

    result = await _node(client, pool)(
        {
            "query": "터치링 자전거 정가 알려줘.",
            "confirmed_entity": {"entity": confirmed, "forName": "터치링 자전거"},
        }
    )

    assert result == {"entity": confirmed}


async def test_confirmed_entity_does_not_hide_new_same_type_ambiguous_lookup() -> None:
    """이전 모호성 해결에서 확정한 엔티티가 텍스트상 비슷하다는 이유만으로 같은
    타입의 다른 미해결 조회를 대신해서는 안 된다. 예를 들어 확정된
    "Mountain-100 Black, 38"은 새로운 "Mountain-100" 언급에도 정상적인 상위
    유사도 후보다. 오래된 확정 엔티티로 새 조회를 해결하면 사용자에게
    "Mountain-100"의 구분을 요청하는 대신 잘못된 제품으로 조용히 답하게 된다.
    PR #55 리뷰에서 확인한 문제다."""
    confirmed = {"productId": 100, "productName": "Mountain-100 Black, 38"}
    client = MockOpenAIClient(
        _entity_response({"entityType": "product", "entityName": "Mountain-100"})
    )
    pool = MockAsyncPostgresPool(
        rows_by_name={"Mountain-100 Black, 38": (100, "Mountain-100 Black, 38")},
        similar_rows_by_name={
            "Mountain-100": [
                (100, "Mountain-100 Black, 38", 0.9),
                (101, "Mountain-100 Silver, 42", 0.9),
            ]
        },
    )

    # confirmed_entity는 이전의 다른 모호성 질문("Mountain 자전거")에 대한
    # 답이었으며, 이번의 새로운 "Mountain-100" 언급에 대한 답이 아니다.
    with pytest.raises(EntityAmbiguousError) as exc_info:
        await _node(client, pool)(
            {
                "query": "Mountain-100 재고 알려줘.",
                "confirmed_entity": {"entity": confirmed, "forName": "Mountain 자전거"},
            }
        )

    assert [c["id"] for c in exc_info.value.candidates] == [100, 101]


async def test_same_name_in_multiple_types_uses_extracted_type_to_disambiguate() -> (
    None
):
    client = MockOpenAIClient(
        _entity_response({"entityType": "supplier", "entityName": "Shared Name"})
    )
    pool = MockAsyncPostgresPool(
        rows_by_name={},
        rows_by_table_and_name={
            ("production.product", "Shared Name"): (81, "Shared Name"),
            ("purchasing.vendor", "Shared Name"): (82, "Shared Name"),
        },
    )

    result = await _node(client, pool)({"query": "Shared Name"})

    assert result == {"entity": {"supplierId": 82, "supplierName": "Shared Name"}}
    assert any("FROM production.product " in query for query, _ in pool.queries)
    assert any("FROM purchasing.vendor " in query for query, _ in pool.queries)


async def test_malformed_and_unknown_tool_calls_are_extraction_failures() -> None:
    malformed = _entity_response({"entityType": "product", "entityName": "Broken"})
    assert malformed.choices[0].message.tool_calls is not None
    malformed.choices[0].message.tool_calls[0].function.arguments = "{broken"

    with pytest.raises(EntityExtractionError):
        await _node(
            MockOpenAIClient(malformed), MockAsyncPostgresPool(rows_by_name={})
        )({"query": "broken"})

    unknown = make_tool_call_response("other_tool", {})
    with pytest.raises(EntityExtractionError):
        await _node(MockOpenAIClient(unknown), MockAsyncPostgresPool(rows_by_name={}))(
            {"query": "broken"}
        )


async def test_pg_trgm_unavailable_becomes_not_found() -> None:
    client = MockOpenAIClient(
        _entity_response({"entityType": "product", "entityName": "Absent"})
    )
    pool = MockAsyncPostgresPool(
        rows_by_name={},
        similarity_error=psycopg.errors.UndefinedFunction("missing similarity"),
    )

    with pytest.raises(EntityNotFoundError) as exc_info:
        await _node(client, pool)({"query": "Absent"})
    assert exc_info.value.entity_name == "Absent"
    assert pool.rollback_called is True


@pytest.mark.parametrize(
    ("threshold", "limit", "message"),
    [
        ("-0.1", "5", "between 0 and 1"),
        ("1.1", "5", "between 0 and 1"),
        ("0.3", "0", "between 1 and 100"),
        ("invalid", "5", "must be a number"),
        ("0.3", "invalid", "must be an integer"),
    ],
)
def test_entity_resolution_environment_settings_are_range_checked(
    monkeypatch: pytest.MonkeyPatch,
    threshold: str,
    limit: str,
    message: str,
) -> None:
    monkeypatch.setenv("ENTITY_SIMILARITY_THRESHOLD", threshold)
    monkeypatch.setenv("ENTITY_CANDIDATE_LIMIT", limit)

    with pytest.raises(ValueError, match=message):
        load_entity_resolution_settings()
