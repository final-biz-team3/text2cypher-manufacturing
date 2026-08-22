"""BOM 경로 중복 방지 Cypher의 최소 구조 계약을 테스트한다."""

import pytest

from tests.orchestrator.cypher_assertions import (
    has_product_id_path_uniqueness_guard,
)


@pytest.mark.parametrize(
    "query",
    [
        """MATCH path=(root:Product)-[:REQUIRES_COMPONENT*1..4]->(part:Product)
        WHERE all(item IN nodes(path) WHERE single(candidate IN nodes(path)
            WHERE candidate.productId = item.productId))
        RETURN path""",
        """MATCH path=(root:Product)-[:REQUIRES_COMPONENT*1..4]->(part:Product)
        WHERE all(item IN nodes(path) WHERE size([candidate IN nodes(path)
            WHERE candidate.productId = item.productId]) = 1)
        RETURN path""",
        """MATCH hierarchy=(root:Product)-[:REQUIRES_COMPONENT*1..4]->(part:Product)
        WITH hierarchy, [product IN nodes(hierarchy) | product.productId] AS ids
        WHERE size(ids) = size(apoc.coll.toSet(ids))
        RETURN hierarchy""",
        """MATCH p=(root:Product)-[:REQUIRES_COMPONENT*1..4]->(part:Product)
        WITH p, [node IN nodes(p) | node.productId] AS productIdPath
        WHERE all(index IN range(0, size(productIdPath) - 1)
            WHERE NOT productIdPath[index] IN productIdPath[..index])
        RETURN p""",
        """MATCH p=(root:Product)-[:REQUIRES_COMPONENT*1..4]->(part:Product)
        WITH p, [node IN nodes(p) | node.productId] AS productIds
        WHERE all(i IN range(0, size(productIds) - 1)
            WHERE all(j IN range(0, i - 1)
                WHERE productIds[i] <> productIds[j]))
        RETURN p""",
    ],
    ids=[
        "single-node",
        "filtered-node-list",
        "unique-list-size",
        "prior-list-membership",
        "indexed-list-comparison",
    ],
)
def test_accepts_product_id_path_uniqueness_guards(query: str) -> None:
    """대표적인 중복 방지 표현을 구조 차이와 무관하게 허용한다."""
    assert has_product_id_path_uniqueness_guard(query)


@pytest.mark.parametrize(
    "query",
    [
        """MATCH p=(root:Product)-[:REQUIRES_COMPONENT*1..4]->(part:Product)
        WHERE all(i IN range(0, size(nodes(p)) - 1)
            WHERE NOT nodes(p)[i].productId IN nodes(p)[0..i])
        RETURN p""",
        """MATCH p=(root:Product)-[:REQUIRES_COMPONENT*1..4]->(part:Product)
        WITH p, [node IN nodes(p) | node.productId] AS productIds
        RETURN apoc.coll.toSet(productIds)""",
        """MATCH p=(root:Product)-[:REQUIRES_COMPONENT*1..4]->(part:Product)
        WITH p, [node IN nodes(p) | node.productId] AS productIds
        WHERE size(productIds) > 0
        RETURN p""",
        """MATCH p=(root:Product)-[:REQUIRES_COMPONENT*1..4]->(part:Product)
        WITH p, [node IN nodes(p) | node.productId] AS productIds,
             [node IN nodes(p) | node.productId] AS otherIds
        WHERE size(productIds) = size(apoc.coll.toSet(otherIds))
        RETURN p""",
    ],
    ids=[
        "product-id-compared-with-node-list",
        "deduplication-only-in-return",
        "list-without-uniqueness-guard",
        "different-lists-compared",
    ],
)
def test_rejects_missing_product_id_path_uniqueness_guards(query: str) -> None:
    """중복 방지 조건이 없거나 서로 다른 타입·목록을 비교하면 거부한다."""
    assert not has_product_id_path_uniqueness_guard(query)
