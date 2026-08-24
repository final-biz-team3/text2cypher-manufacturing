import json
from pathlib import Path

import pytest

from guard.natural_language import make_natural_language_guard_node
from ontology.loader import load_term_dictionary
from ontology.normalizer import normalize_query
from tests.mocks.openai import MockOpenAIClient, make_content_response

_PROJECT_ROOT = Path(__file__).resolve().parents[3]


def test_clear_read_request_is_allowed_without_llm_call() -> None:
    client = MockOpenAIClient()
    node = make_natural_language_guard_node(client)

    result = node(
        {
            "query": "제품 목록을 보여줘",
            "normalized_query": "제품 목록을 보여줘",
            "detected_actions": [],
        }
    )

    assert result["execution_allowed"] is True
    assert result["natural_guard"]["decision"] == "ALLOW_READ"
    assert client.calls == []


@pytest.mark.parametrize(
    "query, expected_intent",
    [
        ("제품 목록을 보여주고 새 제품도 등록", "CREATE"),
        ("재고를 확인한 다음 수량을 변경", "UPDATE"),
        ("공급업체를 조회하고 새 업체를 추가", "CREATE"),
    ],
)
def test_mixed_read_and_write_request_is_blocked_without_llm_call(
    query: str, expected_intent: str
) -> None:
    """조회 표현이 함께 있어도 문장 끝의 쓰기 명령을 우선 차단한다."""
    dictionary = load_term_dictionary(
        _PROJECT_ROOT / "ontology" / "manufacturing_terms.yaml"
    )
    client = MockOpenAIClient()
    node = make_natural_language_guard_node(client)

    result = node({"query": query, **normalize_query(query, dictionary)})

    assert result["execution_allowed"] is False
    assert result["natural_guard"]["decision"] == "BLOCK_WRITE"
    assert result["natural_guard"]["intent"] == expected_intent
    assert client.calls == []


def test_descriptive_write_terms_are_classified_as_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """'삭제된', '변경된'처럼 상태를 설명하는 표현은 쓰기 명령으로 오인하지 않는다."""
    monkeypatch.setenv("OPENAI_MODEL", "test-model")
    read_response = make_content_response(
        '{"intent":"READ","confidence":0.99,"reason":"과거 상태 조회"}'
    )
    client = MockOpenAIClient(read_response, read_response)
    node = make_natural_language_guard_node(client)
    dictionary = load_term_dictionary(
        _PROJECT_ROOT / "ontology" / "manufacturing_terms.yaml"
    )

    for query in ("삭제된 제품을 보여줘", "변경된 가격을 알려줘"):
        result = node({"query": query, **normalize_query(query, dictionary)})
        assert result["execution_allowed"] is True
        assert result["natural_guard"]["decision"] == "ALLOW_READ"

    assert len(client.calls) == 2


def test_write_then_read_mixed_request_uses_llm_and_is_blocked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """쓰기 표현이 문장 중간에 있으면 조회 표현만 보고 허용하지 않는다."""
    monkeypatch.setenv("OPENAI_MODEL", "test-model")
    client = MockOpenAIClient(
        make_content_response(
            '{"intent":"CREATE","confidence":0.99,"reason":"등록과 조회의 혼합 요청"}'
        )
    )
    node = make_natural_language_guard_node(client)
    dictionary = load_term_dictionary(
        _PROJECT_ROOT / "ontology" / "manufacturing_terms.yaml"
    )
    query = "새 제품을 등록하고 제품 목록을 보여줘"

    result = node({"query": query, **normalize_query(query, dictionary)})

    assert result["execution_allowed"] is False
    assert result["natural_guard"]["decision"] == "BLOCK_WRITE"
    assert result["natural_guard"]["intent"] == "CREATE"
    assert len(client.calls) == 1


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


def test_all_twenty_contract_questions_pass_as_read_requests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_MODEL", "test-model")
    contracts = json.loads(
        (_PROJECT_ROOT / "queries" / "query_contracts.json").read_text(encoding="utf-8")
    )
    questions = contracts["questions"]
    dictionary = load_term_dictionary(
        _PROJECT_ROOT / "ontology" / "manufacturing_terms.yaml"
    )
    client = MockOpenAIClient(
        make_content_response(
            '{"intent":"READ","confidence":0.99,"reason":"등록 여부를 조회하는 질문"}'
        )
    )
    node = make_natural_language_guard_node(client)

    assert len(questions) == 20
    for contract in questions:
        query = contract["sampleQuestion"]
        normalized = normalize_query(query, dictionary)
        result = node({"query": query, **normalized})
        assert result["natural_guard"]["decision"] == "ALLOW_READ", contract["id"]

    # RQ05의 "등록되지 않은"은 쓰기 단어가 상태 설명으로 쓰인 경우라
    # 규칙으로 단정하지 않고 LLM에 한 번 확인한다.
    assert len(client.calls) == 1
