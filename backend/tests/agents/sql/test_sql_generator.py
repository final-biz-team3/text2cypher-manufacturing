"""PostgreSQL 쿼리 생성 흐름을 테스트한다."""

import json

from agents.sql.generator import generate_sql
from tests.mocks.openai import MockOpenAIClient, make_content_response


def test_generate_sql_builds_sql_prompt_and_returns_llm_query() -> None:
    """질의 문맥을 SQL 프롬프트에 넣고 생성된 PostgreSQL 문을 반환한다."""
    openai_client = MockOpenAIClient(
        make_content_response(
            "SELECT p.listprice FROM production.product AS p " "WHERE p.productid = 985"
        )
    )

    generated_sql = generate_sql(
        openai_client,
        query="Paint - Black의 정가를 알려줘.",
        entity={"productId": 985, "productName": "Paint - Black"},
        schema_text="production.product {productid: INTEGER, listprice: NUMERIC}",
        business_rules=["확정된 제품 ID로 제품을 조회한다."],
        required_outputs=["productId", "listPrice"],
    )

    assert generated_sql == (
        "SELECT p.listprice FROM production.product AS p WHERE p.productid = 985"
    )
    sent_messages = openai_client.calls[0]["messages"]
    assert "PostgreSQL" in sent_messages[0]["content"]
    assert "production.product {productid: INTEGER" in sent_messages[0]["content"]
    assert "- 확정된 제품 ID로 제품을 조회한다." in sent_messages[0]["content"]
    assert "- productId" in sent_messages[0]["content"]
    assert "- listPrice" in sent_messages[0]["content"]
    assert json.loads(sent_messages[1]["content"]) == {
        "query": "Paint - Black의 정가를 알려줘.",
        "entity": {"productId": 985, "productName": "Paint - Black"},
    }
