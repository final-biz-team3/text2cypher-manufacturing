"""composed_result를 final_answer로 전달하는 얇은 pass-through 노드를 만든다."""

from collections.abc import Callable
from typing import Any

from orchestrator.state import OrchestratorState


def make_generate_answer_node() -> Callable[[OrchestratorState], Any]:
    """LLM 호출 없이 composed_result만 deterministic 문자열로 바꾼다.

    실제 자연어 생성은 다음 작업에서 구현한다.
    """

    async def generate_answer(state: OrchestratorState) -> dict:
        composed_result = state.get("composed_result")
        return {
            "final_answer": (
                f"COMPOSED: {composed_result}" if composed_result is not None else None
            )
        }

    return generate_answer
