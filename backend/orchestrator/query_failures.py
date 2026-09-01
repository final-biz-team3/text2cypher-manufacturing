"""원본 오류를 사용자 안전 실패 분류로 변환하는 경계."""

from orchestrator.state import FailureKind, FailureStage, QueryFailure, ToolName


def make_query_failure(
    *,
    code: str,
    stage: FailureStage,
    category: str,
    kind: FailureKind,
    retryable: bool,
    user_safe_reason: str,
    suggested_action: str,
    failed_tool: ToolName | None = None,
    dependent_failure: bool = False,
) -> QueryFailure:
    return {
        "code": code,
        "stage": stage,
        "category": category,
        "kind": kind,
        "retryable": retryable,
        "user_safe_reason": user_safe_reason,
        "suggested_action": suggested_action,
        "failed_tool": failed_tool,
        "dependent_failure": dependent_failure,
    }


def entity_not_found_failure() -> QueryFailure:
    return make_query_failure(
        code="ENTITY_NOT_FOUND",
        stage="entity_resolution",
        category="NOT_FOUND",
        kind="user_correctable",
        retryable=False,
        user_safe_reason="질문에서 지정한 조회 대상을 데이터에서 식별하지 못했습니다.",
        suggested_action="대상의 정확한 이름이나 식별자를 포함해 다시 질문해 주세요.",
    )


def query_understanding_failure(stage: FailureStage) -> QueryFailure:
    return make_query_failure(
        code="QUERY_UNDERSTANDING_FAILED",
        stage=stage,
        category="UNSUPPORTED",
        kind="user_correctable",
        retryable=False,
        user_safe_reason="현재 질문을 실행 가능한 조회 조건으로 구성하지 못했습니다.",
        suggested_action="조회 대상, 기간, 조건을 조금 더 구체적으로 지정해 주세요.",
    )
