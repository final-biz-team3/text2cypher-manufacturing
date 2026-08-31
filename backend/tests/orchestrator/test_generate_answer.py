"""generate_answer가 composed_result만 final_answer로 전달하는 동작을 테스트한다."""

from orchestrator.nodes.generate_answer import make_generate_answer_node
from orchestrator.state import ComposedResult


async def test_generate_answer_uses_only_composed_result() -> None:
    node = make_generate_answer_node()
    composed_result: ComposedResult = {
        "mode": "joined",
        "rows": [{"id": 1, "stock": 10}],
        "sections": {},
        "error": None,
        "empty_reason": None,
        "total_count": 1,
        "truncated": False,
    }

    result = await node(
        {
            "query": "질의",
            "composed_result": composed_result,
            "sql_result": {"result": [{"count": 10}], "error": None},
            "graph_result": {"result": None, "error": "실행 실패"},
        }
    )

    assert result == {"final_answer": f"COMPOSED: {composed_result}"}


async def test_generate_answer_returns_none_when_no_results() -> None:
    """결과가 하나도 없으면 final_answer도 None이다."""
    node = make_generate_answer_node()

    result = await node(
        {
            "query": "질의",
            "sql_result": {"result": [{"count": 10}], "error": None},
            "graph_result": None,
        }
    )

    assert result == {"final_answer": None}


async def test_generate_answer_hides_internal_composition_error() -> None:
    node = make_generate_answer_node()
    internal_error = "sql_followup의 join key가 바인딩 범위를 벗어났습니다."

    result = await node(
        {
            "query": "질의",
            "composed_result": {
                "mode": "joined",
                "rows": [],
                "sections": {},
                "error": internal_error,
                "empty_reason": None,
                "total_count": 0,
                "truncated": False,
            },
        }
    )

    assert result["final_answer"] is not None
    assert internal_error not in result["final_answer"]
    assert "다시 시도" in result["final_answer"]
