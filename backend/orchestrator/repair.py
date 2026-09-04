"""자기수정 모델에 전달할 안전하고 구조화된 실패 문맥."""

from typing import Literal, TypedDict

from orchestrator.state import FailureStage, QueryFailure, ToolName

RepairEngine = Literal["v1", "v2"]


class RepairContext(TypedDict):
    tool: ToolName
    attempt: int
    issue_code: str
    failed_stage: FailureStage
    safe_error: str
    exact_failure: str
    required_outputs: list[str]
    repair_instructions: list[str]
    previous_issue_codes: list[str]


def make_repair_context(
    *,
    tool: ToolName,
    attempt: int,
    failure: QueryFailure,
    exact_failure: str,
    required_outputs: list[str],
    repair_instructions: tuple[str, ...],
    previous_issue_codes: list[str],
) -> RepairContext:
    return {
        "tool": tool,
        "attempt": attempt,
        "issue_code": failure["code"],
        "failed_stage": failure["stage"],
        "safe_error": failure["user_safe_reason"],
        "exact_failure": exact_failure,
        "required_outputs": list(required_outputs),
        "repair_instructions": list(repair_instructions),
        "previous_issue_codes": [*previous_issue_codes, failure["code"]],
    }


def render_repair_feedback(context: RepairContext) -> str:
    """원본 DB 오류 없이 모델이 재현 가능하게 읽을 수 있는 수정 지침을 만든다."""
    outputs = ", ".join(context["required_outputs"]) or "없음"
    history = ", ".join(context["previous_issue_codes"])
    instructions = "\n".join(
        f"- {instruction}" for instruction in context["repair_instructions"]
    )
    return (
        "Structured repair context\n"
        "Repair only the identified defect. Preserve every unrelated clause, "
        "filter, relationship direction, aggregation, ordering, and parameter.\n"
        f"Tool: {context['tool']}\n"
        f"Issue code: {context['issue_code']}\n"
        f"Failed stage: {context['failed_stage']}\n"
        f"Exact failure: {context['exact_failure']}\n"
        f"Required outputs: {outputs}\n"
        f"Previous issue codes: {history}\n"
        "Repair instructions:\n"
        f"{instructions}"
    )
