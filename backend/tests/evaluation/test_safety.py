import pytest

from evaluation.errors import QuerySafetyError
from evaluation.safety import validate_read_only_cypher, validate_read_only_sql


def test_allows_single_read_only_queries_with_keywords_in_literals() -> None:
    validate_read_only_sql("SELECT 'delete; update' AS note;")
    validate_read_only_cypher("MATCH (n {name: 'CREATE; SET'}) RETURN n")


@pytest.mark.parametrize(
    "query",
    [
        "DELETE FROM production.product",
        "SELECT 1; SELECT 2",
        "WITH changed AS (UPDATE x SET y = 1 RETURNING *) SELECT * FROM changed",
    ],
)
def test_rejects_sql_writes_and_multiple_statements(query: str) -> None:
    with pytest.raises(QuerySafetyError):
        validate_read_only_sql(query)


@pytest.mark.parametrize(
    "query",
    ["CREATE (n:Product)", "MATCH (n) SET n.name = 'x' RETURN n", "CALL db.labels()"],
)
def test_rejects_cypher_writes_and_procedure_calls(query: str) -> None:
    with pytest.raises(QuerySafetyError):
        validate_read_only_cypher(query)
