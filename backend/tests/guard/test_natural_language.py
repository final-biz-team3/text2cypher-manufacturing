from guard.natural_language import make_natural_language_guard_node
from tests.mocks.openai import MockOpenAIClient, make_content_response


def test_clear_read_request_is_allowed_without_llm_call() -> None:
    client = MockOpenAIClient()
    node = make_natural_language_guard_node(client)

    result = node(
        {
            "query": "삭제된 제품을 보여줘",
            "normalized_query": "삭제된 제품을 보여줘",
            "detected_actions": [],
        }
    )

    assert result["execution_allowed"] is True
    assert result["natural_guard"]["decision"] == "ALLOW_READ"
    assert client.calls == []


def test_clear_write_request_is_blocked_without_llm_call() -> None:
    client = MockOpenAIClient()
    node = make_natural_language_guard_node(client)

    result = node(
        {
            "query": "제품을 삭제해줘",
            "normalized_query": "제품을 삭제해줘",
            "detected_actions": [
                {
                    "original": "삭제",
                    "canonical": "삭제",
                    "action_type": "DELETE",
                    "default_policy": "BLOCK",
                }
            ],
        }
    )

    assert result["execution_allowed"] is False
    assert result["natural_guard"]["decision"] == "BLOCK_WRITE"
    assert result["natural_guard"]["intent"] == "DELETE"
    assert client.calls == []


def test_ambiguous_request_uses_llm_structured_classification(
    monkeypatch,
) -> None:
    monkeypatch.setenv("OPENAI_MODEL", "test-model")
    client = MockOpenAIClient(
        make_content_response(
            '{"intent":"UNKNOWN","confidence":0.4,"reason":"의도가 불명확함"}'
        )
    )
    node = make_natural_language_guard_node(client)

    result = node({"query": "재고 정리", "normalized_query": "재고 정리"})

    assert result["execution_allowed"] is False
    assert result["natural_guard"]["decision"] == "NEEDS_CLARIFICATION"
    assert len(client.calls) == 1
