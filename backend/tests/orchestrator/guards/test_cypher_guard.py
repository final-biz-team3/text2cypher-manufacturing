"""Cypher 쿼리 가드가 쓰기 절과 미허가 Label/RelationshipType을 차단하는지 검증한다."""

from agents.cypher.schema.models import GraphSchema
from orchestrator.guards.cypher_guard import make_cypher_guard

_SCHEMA = GraphSchema.model_validate(
    {
        "nodes": {
            "Product": {"properties": {"productId": {"type": "INTEGER"}}},
            "Supplier": {"properties": {"supplierId": {"type": "INTEGER"}}},
        },
        "relationships": {
            "SUPPLIES": {
                "from": "Supplier",
                "to": "Product",
                "properties": {},
            },
        },
    }
)


def test_cypher_guard_allows_plain_match() -> None:
    guard = make_cypher_guard(_SCHEMA)

    result = guard("MATCH (p:Product)<-[:SUPPLIES]-(s:Supplier) RETURN p, s")

    assert result.allowed is True


def test_cypher_guard_blocks_create() -> None:
    guard = make_cypher_guard(_SCHEMA)

    result = guard("CREATE (p:Product {productId: 1}) RETURN p")

    assert result.allowed is False
    assert result.reason_code == "WRITE_KEYWORD_DETECTED"


def test_cypher_guard_blocks_delete_and_detach_delete() -> None:
    guard = make_cypher_guard(_SCHEMA)

    result = guard("MATCH (p:Product) DETACH DELETE p")

    assert result.allowed is False
    assert result.reason_code == "WRITE_KEYWORD_DETECTED"


def test_cypher_guard_blocks_unknown_label() -> None:
    guard = make_cypher_guard(_SCHEMA)

    result = guard("MATCH (u:User) RETURN u")

    assert result.allowed is False
    assert result.reason_code == "UNKNOWN_LABEL_OR_RELATIONSHIP"


def test_cypher_guard_blocks_unknown_relationship_type() -> None:
    guard = make_cypher_guard(_SCHEMA)

    result = guard("MATCH (p:Product)-[:OWNS]->(s:Supplier) RETURN p, s")

    assert result.allowed is False
    assert result.reason_code == "UNKNOWN_LABEL_OR_RELATIONSHIP"


def test_cypher_guard_does_not_false_positive_on_property_named_set() -> None:
    """SET이라는 단어가 속성명 등 다른 문맥에 있어도(단어 경계 밖) 오탐하지 않는다."""
    guard = make_cypher_guard(_SCHEMA)

    result = guard("MATCH (p:Product) WHERE p.name = 'Toolset' RETURN p")

    assert result.allowed is True
