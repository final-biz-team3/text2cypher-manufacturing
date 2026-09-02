"""Cypher graph-pattern contracts that protect Neo4j traversal semantics."""

import pytest

from orchestrator.cypher_contracts import (
    has_coupled_independent_bom_paths,
    has_relationship_list_used_as_path,
)


@pytest.mark.parametrize(
    "query",
    [
        """MATCH pA = (a:Product)-[:REQUIRES_COMPONENT*1..4]->(c:Product),
                  pB = (b:Product)-[:REQUIRES_COMPONENT*1..4]->(c)
             RETURN c""",
        """OPTIONAL MATCH
                  (root:Product)-[:REQUIRES_COMPONENT*1..4]->(left:Product),
                  (root)-[:REQUIRES_COMPONENT*1..4]->(right:Product)
             RETURN left, right""",
        """MATCH pA = (a:Product {name: 'comma, MATCH'})
                        -[:REQUIRES_COMPONENT*1..4]->(c:Product),
                  pB = (b:Product {productId: coalesce(775, 0)})
                        -[:REQUIRES_COMPONENT*1..4]->(c)
             RETURN c""",
        """MATCH pA = (a:Product {
                        tags: [x IN range(1, 5) WHERE x > 2]
                      })-[:REQUIRES_COMPONENT*1..4]->(c:Product),
                  pB = (b:Product)-[:REQUIRES_COMPONENT*1..4]->(c)
             RETURN c""",
    ],
    ids=["converging", "fan-out", "nested-commas", "nested-where"],
)
def test_detects_coupled_independent_bom_paths(query: str) -> None:
    assert has_coupled_independent_bom_paths(query)


@pytest.mark.parametrize(
    "query",
    [
        """MATCH pA = (a:Product)-[:REQUIRES_COMPONENT*1..4]->(c:Product)
             WITH a, c, min(length(pA)) AS minDepthA
             MATCH pB = (b:Product)-[:REQUIRES_COMPONENT*1..4]->(c)
             RETURN c, minDepthA, min(length(pB)) AS minDepthB""",
        """MATCH pA = (a:Product)-[:REQUIRES_COMPONENT*1..2]->(middle:Product),
                  pB = (middle)-[:REQUIRES_COMPONENT*1..2]->(c:Product)
             RETURN c""",
        """MATCH (a:Product)-[:REQUIRES_COMPONENT]->(c:Product),
                  (b:Product)-[:REQUIRES_COMPONENT]->(c)
             RETURN c""",
        """MATCH p = (a:Product)-[:REQUIRES_COMPONENT*1..4]->(c:Product)
             RETURN c""",
        """MATCH pA = (a:Product)-[:REQUIRES_COMPONENT*1..4]->(c:Product)
             WHERE a.active
             MATCH pB = (b:Product)-[:REQUIRES_COMPONENT*1..4]->(c)
             RETURN c""",
    ],
    ids=[
        "separate-match",
        "chained-paths",
        "fixed-length",
        "single-path",
        "top-level-where-boundary",
    ],
)
def test_allows_non_coupled_bom_paths(query: str) -> None:
    assert not has_coupled_independent_bom_paths(query)


@pytest.mark.parametrize(
    "query",
    [
        "MATCH (a)-[path:REL*1..4]->(b) WHERE all(r IN relationships(path) WHERE r.ok) RETURN b",
        "MATCH (a)-[rels:REL*]->(b) RETURN nodes(rels)",
        "MATCH (a)-[edges:REL*2..]->(b) RETURN length(edges)",
        "MATCH (a)-[rels:REL*]->(b) WITH a, b, rels RETURN length(rels)",
        "MATCH (a)-[rels:REL*]->(b) WITH rels AS edges RETURN nodes(edges)",
        "MATCH (a)-[rels:REL*]->(b) WITH * RETURN relationships(rels)",
    ],
    ids=[
        "same-scope-relationships",
        "same-scope-nodes",
        "same-scope-length",
        "with-projection",
        "with-alias",
        "with-star",
    ],
)
def test_detects_relationship_list_used_as_path(query: str) -> None:
    assert has_relationship_list_used_as_path(query)


@pytest.mark.parametrize(
    "query",
    [
        "MATCH path = (a)-[:REL*1..4]->(b) RETURN relationships(path)",
        "MATCH (a)-[rels:REL*]->(b) RETURN rels",
        "MATCH (a)-[rel:REL]->(b) RETURN rel",
        "MATCH (a)-[rels:REL*]->(b) WHERE 'relationships(rels)' = 'text' RETURN b",
        """MATCH (a)-[rels:REL*1..4]->(b)
             WITH a, b
             MATCH rels = (x)-[:OTHER*1..2]->(y)
             RETURN length(rels)""",
        """MATCH (a)-[rels:REL*1..4]->(b)
             WITH a, b
             MATCH rels = (x)-[:OTHER*1..2]->(y)
             RETURN relationships(rels)""",
        """MATCH (a)-[rels:REL*1..4]->(b)
             RETURN b
             UNION
             MATCH rels = (x)-[:OTHER*1..2]->(y)
             RETURN length(rels) AS b""",
    ],
    ids=[
        "path-variable",
        "raw-relationship-list",
        "fixed-relationship",
        "masked-string",
        "dropped-and-rebound-length",
        "dropped-and-rebound-relationships",
        "union-scope-reset",
    ],
)
def test_allows_valid_path_and_relationship_list_usage(query: str) -> None:
    assert not has_relationship_list_used_as_path(query)
