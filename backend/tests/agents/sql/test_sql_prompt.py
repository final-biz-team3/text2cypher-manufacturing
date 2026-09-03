"""PostgreSQL 프롬프트 출처와 구조 계약을 테스트한다."""

import json

from agents.sql.prompt import build_sql_prompt


def test_sql_prompt_uses_physical_schema_and_semantic_catalog_context() -> None:
    messages = build_sql_prompt(
        query="가상 제품의 현재 값을 알려줘.",
        entity={"productId": 4101},
        schema_text="production.synthetic {productid: INTEGER}",
        semantic_context=(
            "actualStock | kind=aggregate | operation=sum | inputs=quantity | "
            "grain=productId"
        ),
        business_rules=["선택한 snapshot 범위만 사용한다."],
        required_outputs=["productId", "actualStock"],
    )

    system = messages[0]["content"]
    assert "PostgreSQL" in system
    assert "읽기 전용" in system
    assert "production.synthetic {productid: INTEGER}" in system
    assert "Semantic output catalog" in system
    assert "operation=sum" in system
    assert "grain=productId" in system
    assert "- 선택한 snapshot 범위만 사용한다." in system
    assert "- productId" in system
    assert "- actualStock" in system
    assert "원문의 filter, comparison, limit, date, quantity" in system
    assert "특정 metric용 순위 recipe를 만들지 않습니다" in system
    assert json.loads(messages[1]["content"]) == {
        "query": "가상 제품의 현재 값을 알려줘.",
        "entity": {"productId": 4101},
    }


def test_sql_prompt_describes_aligned_bindings_without_family_recipe() -> None:
    messages = build_sql_prompt(
        query="선행 행과 정렬된 두 값을 계산해줘.",
        entity=None,
        schema_text="production.synthetic {componentid: INTEGER}",
        input_bindings={
            "componentIds": [10, 10, 20],
            "quantities": [2, 3, 1],
        },
        required_outputs=["componentId"],
    )

    system = messages[0]["content"]
    user = json.loads(messages[1]["content"])
    assert "WITH ORDINALITY" in system
    assert "row alignment" in system
    assert "중복과 NULL" in system
    assert user["inputBindings"] == {
        "componentIds": [10, 10, 20],
        "quantities": [2, 3, 1],
    }
    assert "구매주문 건수가 아니다" not in system
    assert "totalRejectedQty" not in system
    assert "locationid, shelf, bin 순" not in system
