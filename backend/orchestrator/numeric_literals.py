"""아라비아 숫자와 제한된 한글 큰 수 단위를 공통 정규화한다."""

import re
from decimal import Decimal, InvalidOperation

BASE_NUMBER_SOURCE = r"[-+]?\d+(?:,\d{3})*(?:\.\d+)?"
NUMERIC_LITERAL_SOURCE = rf"{BASE_NUMBER_SOURCE}(?:\s*(?:만|억))?"
NUMERIC_LITERAL = re.compile(
    rf"(?<![A-Za-z0-9])(?P<number>{NUMERIC_LITERAL_SOURCE})(?![A-Za-z0-9])"
)

_KOREAN_SCALE = {"만": Decimal(10_000), "억": Decimal(100_000_000)}


def normalize_numeric_literal(value: str) -> str:
    """`1만`, `2.5억`을 DB 숫자 리터럴과 비교 가능한 문자열로 바꾼다."""
    compact = re.sub(r"\s+", "", value).replace(",", "")
    scale = _KOREAN_SCALE.get(compact[-1:])
    if scale is None:
        return compact.removeprefix("+")
    try:
        scaled = Decimal(compact[:-1]) * scale
    except InvalidOperation:
        return compact.removeprefix("+")
    normalized = format(scaled, "f")
    if "." in normalized:
        normalized = normalized.rstrip("0").rstrip(".")
    return normalized.removeprefix("+")


def normalized_numeric_literals(value: str) -> list[str]:
    return [
        normalize_numeric_literal(match.group("number"))
        for match in NUMERIC_LITERAL.finditer(value)
    ]
