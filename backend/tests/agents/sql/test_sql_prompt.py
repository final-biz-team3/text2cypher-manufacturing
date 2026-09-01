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
        required_outputs=["productId", "actualStock"],
    )

    system_content = messages[0]["content"]
    assert messages[0]["role"] == "system"
    assert "PostgreSQL" in system_content
    assert "SELECT" in system_content
    assert "production.product {productid: INTEGER}" in system_content
    assert "해당 식별자로 조회하고 결과에 ID·이름" in system_content
    assert "질문에서 요청한 값" in system_content
    assert "관련 식별자를 기준" in system_content
    assert '"외부 구매 부품"은 production.product.makeflag = false' in system_content
    assert "COALESCE(SUM(quantity), 0)" in system_content
    assert "GREATEST(safetystocklevel - 실제 재고, 0)" in system_content
    assert "totalRejectedQty는 SUM(rejectedqty)" in system_content
    assert "구매주문 건수가 아니다" in system_content
    assert "purchaseorderid 건수를 별도로 계산하거나 정렬 기준으로 쓰지 않는다" in (
        system_content
    )
    assert "실제 재고가 safetystocklevel보다 작은 행만" in system_content
    assert "ID 배열이 이 subquery의 전체 대상" in system_content
    assert "중복은 선행 결과의 행 multiplicity" in system_content
    assert "SELECT DISTINCT로 ID를 집합화" in system_content
    assert "WITH ORDINALITY의 순번을 GROUP BY" in system_content
    assert "shelf·bin별 원본 quantity" in system_content
    assert "locationid, shelf, bin 순" in system_content
    assert "LEFT JOIN" in system_content
    assert "lowerCamelCase" in system_content
    assert '"totalRejectedQty"처럼 SELECT와 같은' in system_content
    assert "double quote로 감쌉니다" in system_content
    assert 'i."actualStock"' in system_content
    assert "quoted lowerCamelCase" in system_content
    assert "내부 alias는 actual_stock 같은 unquoted snake_case" in system_content
    assert '최종 SELECT에서만 "actualStock"' in system_content
    assert "사용자가 순위 번호를 요구한 경우에만" in system_content
    assert "- 확정된 제품 ID로 조회한다." in system_content
    assert "Required output aliases:" in system_content
    assert "- productId" in system_content
    assert "- actualStock" in system_content
    assert "Return every field above using the exact alias." in system_content
    assert "SQL만 반환" in system_content
    assert json.loads(messages[1]["content"]) == {
        "query": "제품의 재고를 알려줘.",
        "entity": {"productId": 985},
    }
