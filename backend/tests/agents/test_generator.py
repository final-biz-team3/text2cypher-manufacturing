"""SQL과 Cypher Agent가 공유하는 LLM 쿼리 호출기를 테스트한다."""

import pytest

from agents.generator import generate_query
from tests.mocks.openai import (
    MockChatCompletion,
    MockOpenAIClient,
    make_content_response,
    make_no_tool_call_response,
)


async def test_generate_query_returns_trimmed_response_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """설정된 모델과 메시지를 전달하고 응답의 바깥 공백을 제거한다."""
    monkeypatch.setenv("OPENAI_MODEL", "query-generation-model")
    openai_client = MockOpenAIClient(
        make_content_response("\n  SELECT productid FROM production.product  \n")
    )
    messages = [
        {"role": "system", "content": "PostgreSQL 쿼리를 생성하세요."},
        {"role": "user", "content": '{"query": "제품을 알려줘."}'},
    ]

    query = await generate_query(openai_client, messages)

    assert query == "SELECT productid FROM production.product"
    assert openai_client.calls == [
        {
            "model": "query-generation-model",
            "messages": messages,
            "reasoning_effort": "medium",
        }
    ]


async def test_generate_query_accepts_high_reasoning_effort() -> None:
    openai_client = MockOpenAIClient(make_content_response("MATCH (n) RETURN n"))

    await generate_query(openai_client, [], reasoning_effort="high")

    assert openai_client.calls[0]["reasoning_effort"] == "high"


async def test_generate_query_rejects_response_without_choices() -> None:
    """LLM 응답에 선택지가 없으면 원인을 알 수 있는 오류로 거부한다."""
    openai_client = MockOpenAIClient(MockChatCompletion(choices=[]))

    with pytest.raises(ValueError, match="LLM returned no query choices"):
        await generate_query(openai_client, [])


async def test_generate_query_requires_configured_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """모델 설정이 누락되면 다른 모델로 대체하지 않고 즉시 실패한다."""
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    openai_client = MockOpenAIClient(make_content_response("SELECT 1"))

    with pytest.raises(KeyError, match="OPENAI_MODEL"):
        await generate_query(openai_client, [])

    assert openai_client.calls == []


async def test_generate_query_rejects_truncated_response() -> None:
    """LLM 출력이 토큰 한도로 잘리면 불완전한 쿼리로 거부한다."""
    openai_client = MockOpenAIClient(
        make_content_response("SELECT * FROM", finish_reason="length")
    )

    with pytest.raises(ValueError, match="did not finish normally: length"):
        await generate_query(openai_client, [])


async def test_generate_query_rejects_none_content() -> None:
    """LLM 응답 content가 없으면 빈 쿼리로 거부한다."""
    openai_client = MockOpenAIClient(make_no_tool_call_response())

    with pytest.raises(ValueError, match="LLM returned an empty query"):
        await generate_query(openai_client, [])


async def test_generate_query_rejects_blank_content() -> None:
    """LLM 응답이 공백뿐이면 빈 쿼리로 거부한다."""
    openai_client = MockOpenAIClient(make_content_response(" \n\t "))

    with pytest.raises(ValueError, match="LLM returned an empty query"):
        await generate_query(openai_client, [])
