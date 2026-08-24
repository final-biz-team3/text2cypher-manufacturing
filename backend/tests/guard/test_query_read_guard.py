import pytest

from guard.query_read_guard import validate_cypher_read_only, validate_sql_read_only


@pytest.mark.parametrize(
    "query",
    [
        "SELECT * FROM production.product",
        "WITH products AS (SELECT * FROM production.product) SELECT * FROM products;",
        "SELECT 'DELETE FROM product' AS example",
    ],
)
def test_allows_read_only_sql(query: str) -> None:
    assert validate_sql_read_only(query) == []


@pytest.mark.parametrize(
    "query",
    [
        "DELETE FROM production.product",
        "WITH removed AS (DELETE FROM production.product RETURNING *) SELECT * FROM removed",
        "SELECT * FROM production.product FOR UPDATE",
        "SELECT 1; DELETE FROM production.product",
    ],
)
def test_blocks_sql_that_can_write_or_lock(query: str) -> None:
    assert validate_sql_read_only(query)


@pytest.mark.parametrize(
    "query",
    [
        "MATCH (p:Product) RETURN p",
        "OPTIONAL MATCH (p:Product) WHERE p.name = 'DELETE' RETURN p;",
    ],
)
def test_allows_read_only_cypher(query: str) -> None:
    assert validate_cypher_read_only(query) == []


@pytest.mark.parametrize(
    "query",
    [
        "MATCH (p:Product) DELETE p",
        "MERGE (p:Product {productId: 1}) RETURN p",
        "MATCH (p:Product) SET p.name = 'changed' RETURN p",
        "MATCH (p:Product) RETURN p; MATCH (s:Supplier) RETURN s",
        "CALL db.labels() YIELD label RETURN label",
    ],
)
def test_blocks_cypher_that_can_write_or_call(query: str) -> None:
    assert validate_cypher_read_only(query)
