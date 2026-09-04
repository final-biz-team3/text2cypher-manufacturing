# Orchestrator 파이프라인의 도메인 예외 계층


# 모든 도메인 예외의 공통 베이스
class AppError(Exception):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message
        super().__init__(message)


# 질의 대상 이름으로 엔티티를 찾지 못했을 때 발생
class EntityNotFoundError(AppError):
    def __init__(self) -> None:
        super().__init__(
            404,
            "ENTITY_NOT_FOUND",
            "질의 대상을 찾을 수 없습니다. 이름을 다시 확인해 주세요.",
        )


# 유사 후보가 여러 개라 사용자 확인이 필요할 때 발생
# 이번 범위(resolve_entity의 정확 일치 매칭)에서는 raise되지 않는다
class EntityAmbiguousError(AppError):
    def __init__(self, candidates: list) -> None:
        super().__init__(
            200,
            "ENTITY_AMBIGUOUS",
            "비슷한 이름이 여러 개 있습니다. 아래 후보 중 하나를 선택해 주세요.",
        )
        self.candidates = candidates


class AnswerGenerationError(AppError):
    """조회는 끝났지만 사용자용 자연어 답변을 신뢰할 수 없을 때 사용한다."""

    def __init__(self) -> None:
        super().__init__(
            502,
            "ANSWER_GENERATION_FAILED",
            "자연어 답변을 생성하지 못했습니다. 잠시 후 다시 시도해 주세요.",
        )


class QueryInfrastructureError(AppError):
    """질문 수정으로 해결할 수 없는 조회 인프라 장애."""

    def __init__(self) -> None:
        super().__init__(
            503,
            "QUERY_INFRASTRUCTURE_UNAVAILABLE",
            "조회 시스템에 일시적으로 연결할 수 없습니다. 잠시 후 다시 시도해 주세요.",
        )
