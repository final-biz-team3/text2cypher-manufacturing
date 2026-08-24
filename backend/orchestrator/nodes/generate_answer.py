"""sql_result·graph_result를 final_answer로 조합하는 얇은 pass-through 노드를 만든다."""

from collections.abc import Callable

from orchestrator.state import OrchestratorState


def make_generate_answer_node() -> Callable[[OrchestratorState], dict]:
    """LLM 호출 없이 sql_result/graph_result를 final_answer 문자열로 합치는 노드를 만든다."""

    def generate_answer(state: OrchestratorState) -> dict:
        parts = []
        sql_result = state.get("sql_result")
        if sql_result is not None:
            parts.append(f"SQL: {sql_result}")
        graph_result = state.get("graph_result")
        if graph_result is not None:
            parts.append(f"GRAPH: {graph_result}")
        return {"final_answer": " / ".join(parts) if parts else None}

    return generate_answer
