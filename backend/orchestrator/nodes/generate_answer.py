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
        if composed_result is not None and composed_result.get("error") is not None:
            return {
                "final_answer": (
                    "요청한 결과를 안전하게 조합하지 못했습니다. "
                    "질의를 조금 더 구체적으로 바꿔 다시 시도해 주세요."
                )
            }
        return {
            "final_answer": (
                f"COMPOSED: {composed_result}" if composed_result is not None else None
            )
        }

    return generate_answer
