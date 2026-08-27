"""OpenAI 클라이언트 싱글턴 생성 기능을 테스트한다."""

from openai import AsyncOpenAI

from core.openai_client import get_openai_client


def test_get_openai_client_returns_async_openai_instance(monkeypatch) -> None:
    """환경변수의 API 키로 AsyncOpenAI 클라이언트 인스턴스를 반환한다."""
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    import core.openai_client as openai_client_module

    openai_client_module._client = None

    client = get_openai_client()

    assert isinstance(client, AsyncOpenAI)


def test_get_openai_client_returns_same_instance_on_repeated_calls(monkeypatch) -> None:
    """반복 호출해도 같은 인스턴스를 재사용한다."""
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    import core.openai_client as openai_client_module

    openai_client_module._client = None

    first = get_openai_client()
    second = get_openai_client()

    assert first is second
