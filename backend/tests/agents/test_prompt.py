"""SQL과 Cypher Agent가 공유하는 프롬프트 메시지 조립을 테스트한다."""

import json

from agents.prompt import build_prompt_messages


def test_build_prompt_messages_separates_trusted_context_and_user_input() -> None:
    """지침·스키마·업무 규칙은 system, 질의·entity는 user에 배치한다."""
    messages = build_prompt_messages(
        instructions="읽기 전용 쿼리를 생성하세요.",
        query="제품의 재고를 알려줘.",
        entity={"productId": 985, "productName": "Paint - Black"},
        schema_text="Product {productId: INTEGER}",
        business_rules=["재고는 수량의 합계다.", "재고가 없으면 0이다."],
    )

    assert messages[0]["role"] == "system"
    assert messages[0]["content"] == """읽기 전용 쿼리를 생성하세요.

Schema:
Product {productId: INTEGER}

Business rules:
- 재고는 수량의 합계다.
- 재고가 없으면 0이다."""
    assert messages[1]["role"] == "user"
    assert json.loads(messages[1]["content"]) == {
        "query": "제품의 재고를 알려줘.",
        "entity": {"productId": 985, "productName": "Paint - Black"},
    }
    assert "제품의 재고를 알려줘." in messages[1]["content"]


def test_build_prompt_messages_includes_feedback_with_empty_previous_error() -> None:
    """previous_error가 빈 문자열이어도(메시지 없는 예외) previous_query가 있으면
    재시도 피드백 섹션을 누락하지 않는다."""
    messages = build_prompt_messages(
        instructions="쿼리를 생성하세요.",
        query="제품 수를 알려줘.",
        entity=None,
        schema_text="Product {}",
        previous_query="SELECT * FROM bad",
        previous_error="",
    )

    assert "Previous attempt failed" in messages[0]["content"]
    assert "SELECT * FROM bad" in messages[0]["content"]


def test_build_prompt_messages_omits_empty_business_rules_section() -> None:
    """업무 규칙이 없으면 불필요한 영역을 만들지 않는다."""
    messages = build_prompt_messages(
        instructions="쿼리를 생성하세요.",
        query="전체 개수를 알려줘.",
        entity=None,
        schema_text="Product {}",
    )

    assert messages[0]["content"] == """쿼리를 생성하세요.

Schema:
Product {}"""
    assert json.loads(messages[1]["content"]) == {
        "query": "전체 개수를 알려줘.",
        "entity": None,
    }
