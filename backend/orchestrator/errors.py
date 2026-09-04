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
    def __init__(self, entity_name: str | None = None) -> None:
        self.entity_name = entity_name
        message = (
            f"'{entity_name}'을(를) 찾을 수 없습니다. 이름을 다시 확인해 주세요."
            if entity_name
            else "질의 대상을 찾을 수 없습니다. 이름을 다시 확인해 주세요."
        )
        super().__init__(404, "ENTITY_NOT_FOUND", message)


# 유사 후보가 여러 개라 사용자 확인이 필요할 때 발생
# 이번 범위(resolve_entity의 정확 일치 매칭)에서는 raise되지 않는다
class EntityAmbiguousError(AppError):
    def __init__(self, candidates: list, lookup_name: str) -> None:
        super().__init__(
            200,
            "ENTITY_AMBIGUOUS",
            "비슷한 이름이 여러 개 있습니다. 아래 후보 중 하나를 선택해 주세요.",
        )
        self.candidates = candidates
        # 클라이언트가 사용자의 선택을 confirmed_entity로 되돌려보낼 때 함께
        # 실어 보내는 상관관계 키(이번에 모호했던 원문 그대로의 추출 이름).
        # 이게 없으면 재확인 요청의 confirmed_entity가 "이번에 답하는 모호함
        # 질문"에 대한 응답인지, 이름이 우연히 비슷한 별개의 새 대상인지 텍스트
        # 유사도만으로는 구분할 수 없다(PR #55 리뷰 - josephuk77).
        self.lookup_name = lookup_name


# self-correction 재시도 횟수 초과용으로 정의됐으나, 재시도 루프는 소진 시에도
# raise하지 않고 error 필드를 유지한 채 정상 종료하도록 구현돼 실제로는 쓰이지 않는다
class RetryExceededError(AppError):
    def __init__(self) -> None:
        super().__init__(
            422,
            "RETRY_EXCEEDED",
            "질의를 처리하지 못했습니다. 질문을 더 구체적으로 입력해 주세요.",
        )


class AnswerGenerationError(AppError):
    """조회는 끝났지만 사용자용 자연어 답변을 신뢰할 수 없을 때 사용한다."""

    def __init__(
        self,
        *,
        reason: str = "answer_generation_error",
        attempt_count: int = 0,
        validation_rejected: bool = False,
    ) -> None:
        self.reason = reason
        self.attempt_count = attempt_count
        self.validation_rejected = validation_rejected
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
