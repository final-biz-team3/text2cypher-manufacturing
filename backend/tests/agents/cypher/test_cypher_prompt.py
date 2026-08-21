"""Text-to-Cypher 프롬프트 구성 동작을 테스트한다."""

import json

from agents.cypher.prompt import build_cypher_prompt


def test_build_cypher_prompt_adds_neo4j_policy_and_dynamic_context() -> None:
    """Cypher 고유 규칙과 요청별 그래프 스키마·업무 문맥을 포함한다."""
    messages = build_cypher_prompt(
        query="이 부품을 사용하는 완제품을 알려줘.",
        entity={"productId": 492},
        schema_text="(:Product)-[:REQUIRES_COMPONENT]->(:Product)",
        business_rules=["관계 방향은 조립품에서 부품 방향이다."],
    )

    system_content = messages[0]["content"]
    assert messages[0]["role"] == "system"
    assert "Neo4j Cypher" in system_content
    assert "관계 방향" in system_content
    assert "(:Product)-[:REQUIRES_COMPONENT]->(:Product)" in system_content
    assert "- 관계 방향은 조립품에서 부품 방향이다." in system_content
    assert "Cypher만 반환" in system_content
    assert json.loads(messages[1]["content"]) == {
        "query": "이 부품을 사용하는 완제품을 알려줘.",
        "entity": {"productId": 492},
    }
