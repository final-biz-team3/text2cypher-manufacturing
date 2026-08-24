"""Text-to-SQL 프롬프트 구성 동작을 테스트한다."""

import json

from agents.sql.prompt import build_sql_prompt


def test_build_sql_prompt_adds_postgresql_policy_and_dynamic_context() -> None:
    """SQL 고유 규칙과 요청별 스키마·업무 문맥을 메시지에 포함한다."""
    messages = build_sql_prompt(
        query="제품의 재고를 알려줘.",
        entity={"productId": 985},
        schema_text="production.product {productid: INTEGER}",
        business_rules=["확정된 제품 ID로 조회한다."],
    )

    system_content = messages[0]["content"]
    assert messages[0]["role"] == "system"
    assert "PostgreSQL" in system_content
    assert "SELECT" in system_content
    assert "production.product {productid: INTEGER}" in system_content
    assert "해당 식별자로 조회하고 결과에 ID·이름" in system_content
    assert "질문에서 요청한 값" in system_content
    assert "관련 식별자를 기준" in system_content
    assert "COALESCE(SUM(quantity), 0)" in system_content
    assert "GREATEST(safetystocklevel - 실제 재고, 0)" in system_content
    assert "shelf·bin별 원본 quantity" in system_content
    assert "locationid, shelf, bin 순" in system_content
    assert "LEFT JOIN" in system_content
    assert "- 확정된 제품 ID로 조회한다." in system_content
    assert "SQL만 반환" in system_content
    assert json.loads(messages[1]["content"]) == {
        "query": "제품의 재고를 알려줘.",
        "entity": {"productId": 985},
    }
