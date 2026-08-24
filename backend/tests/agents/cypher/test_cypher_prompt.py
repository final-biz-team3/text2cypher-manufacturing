"""Text-to-Cypher 프롬프트 구성 동작을 테스트한다."""

import json

from agents.cypher.prompt import build_cypher_prompt
from agents.cypher.schema.models import GraphQueryPolicy


def test_build_cypher_prompt_adds_neo4j_policy_and_dynamic_context() -> None:
    """Cypher 고유 규칙과 요청별 그래프 스키마·업무 문맥을 포함한다."""
    messages = build_cypher_prompt(
        query="이 부품을 사용하는 완제품을 알려줘.",
        entity={"productId": 492},
        schema_text="(:Product)-[:REQUIRES_COMPONENT]->(:Product)",
        query_policy=GraphQueryPolicy(
            bomAsOfDate="2014-08-08",
            bomMaxDepth=4,
        ),
        business_rules=["같은 완제품의 서로 다른 경로는 별도로 보존한다."],
    )

    system_content = messages[0]["content"]
    assert messages[0]["role"] == "system"
    assert "Neo4j Cypher" in system_content
    assert "관계 방향" in system_content
    assert "RETURN 절" in system_content
    assert "(:Product)-[:REQUIRES_COMPONENT]->(:Product)" in system_content
    assert "상위 조립품에서 하위 부품 방향" in system_content
    assert "부품의 사용처는 역방향" in system_content
    assert "완제품의 하위 부품" in system_content
    assert "BOM 가변 길이 경로는 최대 4단계" in system_content
    assert "2014-08-08 기준" in system_content
    assert "startDate <= 기준일" in system_content
    assert "기준일 < endDate" in system_content
    assert "sellableFinishedGood = true" in system_content
    assert "시작·도착 Product의 ID·이름" in system_content
    assert "전체 Product ID·이름 경로" in system_content
    assert "경로 노드의 productId 목록을 기준으로 중복" in system_content
    assert "productId 값과 Node 목록을 직접 비교하지 않는다" in system_content
    assert "깊이, 도착 Product ID, 전체 ID 경로 순" in system_content
    assert "- 같은 완제품의 서로 다른 경로는 별도로 보존한다." in system_content
    assert "Cypher만 반환" in system_content
    assert json.loads(messages[1]["content"]) == {
        "query": "이 부품을 사용하는 완제품을 알려줘.",
        "entity": {"productId": 492},
    }
