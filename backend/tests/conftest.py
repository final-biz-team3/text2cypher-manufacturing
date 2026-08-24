"""OPENAI_MODEL이 없는 단위 테스트에 테스트용 모델명을 주입한다."""

import os

import pytest


@pytest.fixture(autouse=True)
def configure_openai_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """실제 환경 설정이 없는 단위 테스트에만 테스트 모델을 주입한다."""
    if "OPENAI_MODEL" not in os.environ:
        monkeypatch.setenv("OPENAI_MODEL", "test-openai-model")
