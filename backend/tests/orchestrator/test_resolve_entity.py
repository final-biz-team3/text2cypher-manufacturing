"""Explicit entity extraction and database lookup contracts."""

import psycopg
import pytest

from agents.cypher.schema.models import GraphSchema
from orchestrator.errors import EntityAmbiguousError, EntityNotFoundError
from orchestrator.nodes.resolve_entity import (
    EntityExtractionError,
    EntityResolutionSettings,
    load_entity_resolution_settings,
    make_resolve_entity_node,
)
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


async def test_no_tool_call_returns_none_without_raw_query_database_scan() -> None:
    client = MockOpenAIClient(make_no_tool_call_response())
    pool = MockAsyncPostgresPool(
        rows_by_name={"A Name": (1, "A Name"), "Long A Name": (2, "Long A Name")}
    )

    result = await _node(client, pool)(
        {"query": "A Name과 Long A Name을 문장에 우연히 쓴다"}
    )

    assert result == {"entity": None}
    assert pool.last_query is None


@pytest.mark.parametrize("name", ["제품", "product"])
async def test_exact_schema_type_alias_is_ignored_as_generic(name: str) -> None:
    client = MockOpenAIClient(
        make_tool_call_response(
            "extract_entity", {"entityType": "product", "entityName": name}
        )
    )
    pool = MockAsyncPostgresPool(rows_by_name={})

    assert await _node(client, pool)({"query": "제품 수"}) == {"entity": None}
    assert pool.last_query is None


async def test_whitespace_delimited_type_prefix_and_suffix_are_stripped() -> None:
    client = MockOpenAIClient(
        make_tool_calls_response(
            [
                (
                    "extract_entity",
                    {"entityType": "product", "entityName": "제품 Cinder Bolt"},
                ),
                (
                    "extract_entity",
                    {"entityType": "supplier", "entityName": "North Foundry 공급사"},
                ),
            ]
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
        make_tool_calls_response(
            [
                (
                    "extract_entity",
                    {"entityType": "product", "entityName": "Short Name"},
                ),
                (
                    "extract_entity",
                    {"entityType": "product", "entityName": "Short Name Extended"},
                ),
                (
                    "extract_entity",
                    {"entityType": "product", "entityName": "Short Name"},
                ),
            ]
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
        make_tool_calls_response(
            [
                (
                    "extract_entity",
                    {"entityType": "product", "entityName": "Known"},
                ),
                (
                    "extract_entity",
                    {"entityType": "product", "entityName": "Absent"},
                ),
            ]
        )
    )
    pool = MockAsyncPostgresPool(rows_by_name={"Known": (41, "Known")})

    with pytest.raises(EntityNotFoundError):
        await _node(client, pool)({"query": "Known and Absent"})


async def test_alias_fragments_are_explicit_claims_and_fail_lookup() -> None:
    client = MockOpenAIClient(
        make_tool_calls_response(
            [
                (
                    "extract_entity",
                    {"entityType": "product", "entityName": "완제"},
                ),
                (
                    "extract_entity",
                    {"entityType": "supplier", "entityName": "공급"},
                ),
            ]
        )
    )

    with pytest.raises(EntityNotFoundError):
        await _node(client, MockAsyncPostgresPool(rows_by_name={}))(
            {"query": "two fragments"}
        )


async def test_success_and_similar_candidate_raises_ambiguous() -> None:
    client = MockOpenAIClient(
        make_tool_calls_response(
            [
                (
                    "extract_entity",
                    {"entityType": "product", "entityName": "Known"},
                ),
                (
                    "extract_entity",
                    {"entityType": "supplier", "entityName": "Nort Foundry"},
                ),
            ]
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


@pytest.mark.parametrize("similar", [False, True])
async def test_confirmed_entity_does_not_hide_another_explicit_failure(
    similar: bool,
) -> None:
    confirmed = {"productId": 61, "productName": "Confirmed"}
    client = MockOpenAIClient(
        make_tool_call_response(
            "extract_entity",
            {"entityType": "supplier", "entityName": "Missing Foundry"},
        )
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
        make_tool_call_response(
            "extract_entity",
            {"entityType": "product", "entityName": "Confirmed"},
        )
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
    """A client may resend the original question after the user picks a
    candidate instead of rewriting the query with the resolved name, so the
    same ambiguous wording gets extracted again. Pairing confirmed_entity with
    the exact lookup text it answers (forName) lets this resolve without
    re-raising EntityAmbiguousError, without relying on the confirmed row
    still ranking within the small top-N shown to the user."""
    confirmed = {"productId": 956, "productName": "Touring-1000 Yellow, 54"}
    client = MockOpenAIClient(
        make_tool_call_response(
            "extract_entity",
            {"entityType": "product", "entityName": "터치링 자전거"},
        )
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
    """A confirmed entity from an earlier disambiguation must not silently
    stand in for a different, still-unresolved lookup of the same type just
    because it is textually similar. E.g. a confirmed "Mountain-100 Black, 38"
    is a legitimate top similarity match for a brand new "Mountain-100"
    mention too - resolving that new lookup via the stale confirmed entity
    would silently answer with the wrong product instead of asking the user
    to disambiguate "Mountain-100" (PR #55 review - josephuk77)."""
    confirmed = {"productId": 100, "productName": "Mountain-100 Black, 38"}
    client = MockOpenAIClient(
        make_tool_call_response(
            "extract_entity",
            {"entityType": "product", "entityName": "Mountain-100"},
        )
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

    # confirmed_entity was the answer to an earlier, different ambiguity
    # prompt ("Mountain 자전거"), not to this new "Mountain-100" mention.
    with pytest.raises(EntityAmbiguousError) as exc_info:
        await _node(client, pool)(
            {
                "query": "Mountain-100 재고 알려줘.",
                "confirmed_entity": {"entity": confirmed, "forName": "Mountain 자전거"},
            }
        )

    assert [c["id"] for c in exc_info.value.candidates] == [100, 101]


async def test_same_name_in_multiple_types_uses_only_extracted_type_table() -> None:
    client = MockOpenAIClient(
        make_tool_call_response(
            "extract_entity",
            {"entityType": "supplier", "entityName": "Shared Name"},
        )
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
    assert pool.last_query is not None
    assert "FROM purchasing.vendor" in pool.last_query[0]


async def test_malformed_and_unknown_tool_calls_are_extraction_failures() -> None:
    malformed = make_tool_call_response(
        "extract_entity", {"entityType": "product", "entityName": "Broken"}
    )
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
        make_tool_call_response(
            "extract_entity",
            {"entityType": "product", "entityName": "Absent"},
        )
    )
    pool = MockAsyncPostgresPool(
        rows_by_name={},
        similarity_error=psycopg.errors.UndefinedFunction("missing similarity"),
    )

    with pytest.raises(EntityNotFoundError):
        await _node(client, pool)({"query": "Absent"})
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
