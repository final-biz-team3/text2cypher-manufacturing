"""도메인 예외 클래스의 status_code/code/message를 검증한다."""

from orchestrator.errors import (
    AppError,
    EntityAmbiguousError,
    EntityNotFoundError,
    RetryExceededError,
)


def test_entity_not_found_error_has_404_status() -> None:
    """제품을 찾지 못하면 404와 안내 메시지를 담는다."""
    error = EntityNotFoundError()

    assert isinstance(error, AppError)
    assert error.status_code == 404
    assert error.code == "ENTITY_NOT_FOUND"
    assert "찾을 수 없습니다" in error.message


def test_entity_ambiguous_error_carries_candidates() -> None:
    """후보가 여러 개면 candidates를 그대로 보존한다."""
    error = EntityAmbiguousError(candidates=["Product A", "Product B"])

    assert error.status_code == 200
    assert error.code == "ENTITY_AMBIGUOUS"
    assert error.candidates == ["Product A", "Product B"]


def test_retry_exceeded_error_has_422_status() -> None:
    """재시도 상한 초과는 422다."""
    error = RetryExceededError()

    assert error.status_code == 422
    assert error.code == "RETRY_EXCEEDED"
