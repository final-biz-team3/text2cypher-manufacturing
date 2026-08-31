"""자연어 숫자 조건이 생성 쿼리에 의미까지 보존되는지 검증한다."""

import pytest

from orchestrator.query_conditions import missing_numeric_filter_literals


@pytest.mark.parametrize(
    ("question", "query"),
    [
        ("가격이 0원인 제품", "SELECT * FROM product WHERE price = 0"),
        ("재고가 -1 이하인 제품", "SELECT * FROM product WHERE stock <= -1"),
        ("가격이 10 이상인 제품", "MATCH (p) WHERE p.price >= 10 RETURN p"),
        ("판매량 상위 5개", "SELECT * FROM product ORDER BY sales DESC LIMIT 5"),
        ("2025년 주문", "SELECT * FROM orders WHERE created_at >= '2025-01-01'"),
        ("수량이 0개인 제품", "SELECT * FROM product WHERE quantity = 0"),
    ],
)
def test_numeric_constraint_accepts_matching_query(question: str, query: str) -> None:
    assert missing_numeric_filter_literals(question, query) == set()


@pytest.mark.parametrize(
    ("question", "query"),
    [
        ("가격이 0원인 제품", "SELECT * FROM product LIMIT 0"),
        ("가격이 0 이하인 제품", "SELECT * FROM product WHERE price >= 0"),
        ("재고가 -1 이하인 제품", "SELECT * FROM product WHERE stock <= 1"),
        ("판매량 상위 5개", "SELECT * FROM product WHERE category_id = 5"),
        ("수량이 0개인 제품", "SELECT * FROM product"),
        ("2025년 주문", "SELECT * FROM orders"),
    ],
)
def test_numeric_constraint_rejects_dropped_or_reversed_meaning(
    question: str, query: str
) -> None:
    assert missing_numeric_filter_literals(question, query)
