"""main.py의 전역 예외 처리(AppError, 예상 못한 Exception)를 테스트한다."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from main import app as main_app
from main import unexpected_error_handler


def test_unexpected_exception_returns_safe_500_json() -> None:
    """AppError가 아닌 예외도 트레이스백/내부 정보 노출 없이 안전하게 500을 반환한다."""
    app = FastAPI()
    app.add_exception_handler(Exception, unexpected_error_handler)

    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError("내부 디버그 정보: DB 비밀번호=hunter2")

    client = TestClient(app, raise_server_exceptions=False)

    response = client.get("/boom")

    assert response.status_code == 500
    assert response.json() == {
        "code": "INTERNAL_ERROR",
        "message": "일시적인 오류가 발생했습니다. 잠시 후 다시 시도해 주세요.",
    }
    assert "hunter2" not in response.text


def test_unknown_route_still_returns_404_not_swallowed_into_500() -> None:
    """FastAPI 기본 HTTPException 처리가 범용 Exception 핸들러에 먹히지 않는지 확인한다."""
    client = TestClient(main_app, raise_server_exceptions=False)

    response = client.get("/definitely-not-a-real-route")

    assert response.status_code == 404
