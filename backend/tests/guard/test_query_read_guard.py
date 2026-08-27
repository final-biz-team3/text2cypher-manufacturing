import pytest

from guard.query_read_guard import validate_cypher_read_only, validate_sql_read_only


@pytest.mark.parametrize(
    "query",
    [
        "SELECT * FROM production.product",
        "WITH products AS (SELECT * FROM production.product) SELECT * FROM products;",
        "SELECT 'DELETE FROM product' AS example",
        "SELECT $$ DELETE FROM product $$ AS example",
        "SELECT pg_catalog.count(*) FROM production.product",
        """SELECT p.productid,
                  COALESCE(pg_catalog.sum(i.quantity), 0) AS actual_stock,
                  GREATEST(
                    p.safetystocklevel
                      - COALESCE(pg_catalog.sum(i.quantity), 0),
                    0
                  ) AS shortage
             FROM production.product AS p
             LEFT JOIN production.productinventory AS i
               ON i.productid = p.productid
            GROUP BY p.productid, p.safetystocklevel""",
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
        "SELECT '--'; DELETE FROM production.product",
        "SELECT * INTO audit_copy FROM production.product",
        "SELECT nextval('audit_seq')",
        "SELECT pg_advisory_lock(42)",
        "SELECT * FROM production.product FOR KEY SHARE",
        "SELECT * FROM production.product FOR NO KEY UPDATE",
        "SELECT '\\'; DELETE FROM production.product",
        "SELECT pg_notify('audit', 'x')",
        "SELECT set_config('search_path', 'public', false)",
        "SELECT \"nextval\"('audit_seq')",
        "SELECT custom_write_function()",
        "SELECT attacker.count(*)",
        "SELECT attacker.round(1)",
        "SELECT count(*) FROM production.product",
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


@pytest.mark.parametrize(
    "query",
    [
        "MATCH (n:`safe\\`) SET n.flag = true RETURN n AS `tail`",
        "MATCH (n) WHERE n.name = $tag$ SET n.flag = true RETURN $tag$",
    ],
)
def test_cypher_write_tokens_cannot_be_hidden_by_foreign_escape_rules(
    query: str,
) -> None:
    violations = validate_cypher_read_only(query)

    assert violations
    assert violations[0]["code"] == "WRITE_CLAUSE"
