"""generate_answer 노드가 sql_result/graph_result를 final_answer로 조합하는 동작을 테스트한다."""

from orchestrator.nodes.generate_answer import make_generate_answer_node


def test_generate_answer_combines_sql_and_graph_results() -> None:
    """SQL과 GRAPH 결과가 모두 있으면 둘 다 final_answer에 포함한다."""
    node = make_generate_answer_node()

    result = node(
        {
            "query": "질의",
            "sql_result": {"result": [{"count": 10}], "error": None},
            "graph_result": {"result": None, "error": "실행 실패"},
        }
    )

    assert result == {
        "final_answer": (
            "SQL: {'result': [{'count': 10}], 'error': None} / "
            "GRAPH: {'result': None, 'error': '실행 실패'}"
        )
    }


def test_generate_answer_returns_none_when_no_results() -> None:
    """결과가 하나도 없으면 final_answer도 None이다."""
    node = make_generate_answer_node()

    result = node({"query": "질의", "sql_result": None, "graph_result": None})

    assert result == {"final_answer": None}
