"""BOM 경로 중복 방지 Cypher 검증식을 테스트한다."""

import pytest

from tests.orchestrator.cypher_assertions import (
    has_product_id_path_uniqueness_predicate,
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
            WHERE single(j IN range(0, size(productIds) - 1)
                WHERE productIds[j] = productIds[i]))
        RETURN p""",
        """MATCH p=(root:Product)-[:REQUIRES_COMPONENT*1..4]->(part:Product)
        WHERE all(i IN range(0, size(nodes(p)) - 1)
            WHERE single(j IN range(0, size(nodes(p)) - 1)
                WHERE nodes(p)[j].productId = nodes(p)[i].productId))
        RETURN p""",
        """MATCH p=(root:Product)-[:REQUIRES_COMPONENT*1..4]->(part:Product)
        WITH p, [node IN nodes(p) | node.productId] AS productIds
        WHERE all(i IN range(0, size(productIds) - 1)
            WHERE all(j IN range(i + 1, size(productIds) - 1)
                WHERE productIds[i] <> productIds[j]))
        RETURN p""",
        """MATCH p=(root:Product)-[:REQUIRES_COMPONENT*1..4]->(part:Product)
        WITH p, [node IN nodes(p) | node.productId] AS productIdPath
        WHERE all(i IN range(0, size(productIdPath) - 1)
            WHERE all(j IN range(0, i - 1)
                WHERE productIdPath[i] <> productIdPath[j]))
        RETURN p""",
        """MATCH p=(root:Product)-[:REQUIRES_COMPONENT*1..4]->(part:Product)
        WITH p, [node IN nodes(p) | node.productId] AS productIdPath
        WHERE all(i IN range(0, size(productIdPath) - 1)
            WHERE all(j IN range(0, size(productIdPath) - 1)
                WHERE i = j OR productIdPath[i] <> productIdPath[j]))
        RETURN p""",
    ],
    ids=[
        "single-count",
        "filtered-list-count",
        "unique-list-size",
        "prior-list-slice",
        "single-index-count",
        "single-node-index-count",
        "later-index-pairwise-comparison",
        "earlier-index-pairwise-comparison",
        "all-index-pairwise-comparison",
    ],
)
def test_accepts_product_id_path_uniqueness_predicates(query: str) -> None:
    """변수명과 중복 검사 방식이 달라도 같은 의미의 조건은 허용한다."""
    assert has_product_id_path_uniqueness_predicate(query)


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
        WHERE all(i IN range(0, size(nodes(p)) - 1) WHERE i <> 0)
        RETURN p""",
        """MATCH p=(root:Product)-[:REQUIRES_COMPONENT*1..4]->(part:Product)
        WITH p, [node IN nodes(p) | node.productId] AS productIds,
             [node IN nodes(p) | node.productId] AS otherIds
        WHERE size(productIds) = size(apoc.coll.toSet(otherIds))
        RETURN p""",
        """MATCH p=(root:Product)-[:REQUIRES_COMPONENT*1..4]->(part:Product)
        WITH p, [node IN nodes(p) | node.productId] AS productIds
        WHERE all(i IN range(0, size(productIds) - 1) |
            all(j IN range(i + 1, size(productIds) - 1) |
                productIds[i] <> productIds[j]))
        RETURN p""",
    ],
    ids=[
        "mixed-value-types",
        "toset-without-filter",
        "unrelated-range",
        "mixed-lists",
        "invalid-pairwise-separator",
    ],
)
def test_rejects_non_uniqueness_predicates(query: str) -> None:
    """중복을 막지 않거나 서로 다른 타입·목록을 비교하는 조건은 거부한다."""
    assert not has_product_id_path_uniqueness_predicate(query)
