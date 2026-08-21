"""Text-to-SQL 프롬프트 구성 동작을 테스트한다."""

import json

from agents.sql.prompt import build_sql_prompt


def test_build_sql_prompt_adds_postgresql_policy_and_dynamic_context() -> None:
    """SQL 고유 규칙과 요청별 스키마·업무 문맥을 메시지에 포함한다."""
    messages = build_sql_prompt(
        query="제품의 재고를 알려줘.",
        entity={"productId": 985},
        schema_text="production.product {productid: INTEGER}",
        business_rules=["재고는 quantity 합계다."],
    )

    system_content = messages[0]["content"]
    assert messages[0]["role"] == "system"
    assert "PostgreSQL" in system_content
    assert "SELECT" in system_content
    assert "production.product {productid: INTEGER}" in system_content
    assert "- 재고는 quantity 합계다." in system_content
    assert "SQL만 반환" in system_content
    assert json.loads(messages[1]["content"]) == {
        "query": "제품의 재고를 알려줘.",
        "entity": {"productId": 985},
    }
