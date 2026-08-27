from orchestrator.nodes.validate_generated_queries import (
    route_after_query_guard,
    validate_generated_queries,
)


def test_hybrid_requires_both_queries_to_be_read_only() -> None:
    result = validate_generated_queries(
        {
            "query": "통합 질문",
            "tool_plan": ["sql", "graph"],
            "sql_query": "SELECT 1",
            "cypher_query": "MATCH (p:Product) DELETE p",
        }
    )

    assert result["execution_allowed"] is False
    assert result["query_guard"]["sql_read_only"] is True
    assert result["query_guard"]["cypher_read_only"] is False


def test_sql_plan_passes_when_sql_is_read_only() -> None:
    result = validate_generated_queries(
        {
            "query": "조회 질문",
            "tool_plan": ["sql"],
            "sql_query": "SELECT 1",
            "cypher_query": None,
        }
    )

    assert result["execution_allowed"] is True
    assert result["query_guard"]["decision"] == "PASSED"
    assert route_after_query_guard(result) == "continue"


def test_blocked_query_routes_to_stop() -> None:
    result = validate_generated_queries(
        {
            "query": "쓰기 요청",
            "tool_plan": ["graph"],
            "cypher_query": "MATCH (p:Product) DELETE p RETURN p",
        }
    )

    assert result["execution_allowed"] is False
    assert route_after_query_guard(result) == "stop"
