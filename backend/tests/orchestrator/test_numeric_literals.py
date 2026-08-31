"""한글 큰 수 단위의 공통 숫자 정규화를 검증한다."""

import pytest

from orchestrator.numeric_literals import (
    normalize_numeric_literal,
    normalized_numeric_literals,
)


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("1만", "10000"),
        ("2억", "200000000"),
        ("2.5만", "25000"),
        ("1,000만", "10000000"),
        ("+3만", "30000"),
        ("-1만", "-10000"),
    ],
)
def test_normalize_numeric_literal_supports_korean_scales(
    source: str, expected: str
) -> None:
    assert normalize_numeric_literal(source) == expected


def test_normalized_numeric_literals_keeps_scaled_expression_as_one_value() -> None:
    assert normalized_numeric_literals("가격이 1만 원 이상이고 한도는 2억 원") == [
        "10000",
        "200000000",
    ]
