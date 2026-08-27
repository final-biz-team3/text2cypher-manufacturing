"""Neo4j Cypher 쿼리 생성 흐름을 테스트한다."""

import json

from agents.cypher.generator import generate_cypher
from agents.cypher.schema.models import GraphQueryPolicy
from tests.mocks.openai import MockOpenAIClient, make_content_response


async def test_generate_cypher_builds_cypher_prompt_and_returns_llm_query() -> None:
    """질의 문맥을 Cypher 프롬프트에 넣고 생성된 Neo4j 문을 반환한다."""
    openai_client = MockOpenAIClient(
        make_content_response(
            "MATCH (parent:Product)-[:REQUIRES_COMPONENT]->(part:Product) "
            "WHERE part.productId = 492 RETURN parent"
        )
    )

    generated_cypher = await generate_cypher(
        openai_client,
        query="이 부품을 사용하는 완제품을 알려줘.",
        entity={"productId": 492, "productName": "Paint - Black"},
        schema_text="(:Product)-[:REQUIRES_COMPONENT]->(:Product)",
        query_policy=GraphQueryPolicy(
            bomAsOfDate="2014-08-08",
            bomMaxDepth=4,
        ),
        business_rules=["관계 방향은 조립품에서 부품 방향이다."],
        required_outputs=["componentId", "depth"],
    )

    assert generated_cypher == (
        "MATCH (parent:Product)-[:REQUIRES_COMPONENT]->(part:Product) "
        "WHERE part.productId = 492 RETURN parent"
    )
    sent_messages = openai_client.calls[0]["messages"]
    assert "Neo4j Cypher" in sent_messages[0]["content"]
    assert "RETURN 절" in sent_messages[0]["content"]
    assert "(:Product)-[:REQUIRES_COMPONENT]->(:Product)" in (
        sent_messages[0]["content"]
    )
    assert "- 관계 방향은 조립품에서 부품 방향이다." in (sent_messages[0]["content"])
    assert "- componentId" in sent_messages[0]["content"]
    assert "- depth" in sent_messages[0]["content"]
    assert json.loads(sent_messages[1]["content"]) == {
        "query": "이 부품을 사용하는 완제품을 알려줘.",
        "entity": {"productId": 492, "productName": "Paint - Black"},
    }
