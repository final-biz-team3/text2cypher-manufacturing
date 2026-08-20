"""Orchestrator 파이프라인의 도메인 예외 계층을 정의한다."""


class AppError(Exception):
    """모든 도메인 예외의 공통 베이스."""

    def __init__(self, status_code: int, code: str, message: str) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message
        super().__init__(message)


class EntityNotFoundError(AppError):
    """질의 대상 이름으로 엔티티를 찾지 못했을 때 발생한다."""

    def __init__(self) -> None:
        super().__init__(
            404,
            "ENTITY_NOT_FOUND",
            "질의 대상을 찾을 수 없습니다. 이름을 다시 확인해 주세요.",
        )


class EntityAmbiguousError(AppError):
    """유사 후보가 여러 개라 사용자 확인이 필요할 때 발생한다.

    이번 범위(resolve_entity의 정확 일치 매칭)에서는 raise되지 않는다.
    """

    def __init__(self, candidates: list) -> None:
        super().__init__(
            200,
            "ENTITY_AMBIGUOUS",
            "비슷한 이름이 여러 개 있습니다. 아래 후보 중 하나를 선택해 주세요.",
        )
        self.candidates = candidates


class RetryExceededError(AppError):
    """self-correction 재시도 횟수를 초과했을 때 발생한다.

    이번 범위(self-correction 루프 미구현)에서는 raise되지 않는다.
    """

    def __init__(self) -> None:
        super().__init__(
            422,
            "RETRY_EXCEEDED",
            "질의를 처리하지 못했습니다. 질문을 더 구체적으로 입력해 주세요.",
        )
