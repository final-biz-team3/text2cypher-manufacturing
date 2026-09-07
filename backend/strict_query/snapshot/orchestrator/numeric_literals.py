"""아라비아 숫자와 한글 큰 수 단위를 공통 정규화한다."""

import re
from decimal import Decimal, InvalidOperation

BASE_NUMBER_SOURCE = r"[-+]?\d+(?:,\d{3})*(?:\.\d+)?"
_KOREAN_UNIT_SOURCE = r"(?:천만|억|만|천)"
NUMERIC_LITERAL_SOURCE = (
    rf"{BASE_NUMBER_SOURCE}(?:\s*{_KOREAN_UNIT_SOURCE})"
    rf"(?:\s*{BASE_NUMBER_SOURCE}\s*{_KOREAN_UNIT_SOURCE})*"
    rf"|{BASE_NUMBER_SOURCE}"
)
NUMERIC_LITERAL = re.compile(
    rf"(?<![A-Za-z0-9])(?P<number>(?:{NUMERIC_LITERAL_SOURCE}))(?![A-Za-z0-9])"
)

_KOREAN_SCALE = {
    "천": Decimal(1_000),
    "만": Decimal(10_000),
    "천만": Decimal(10_000_000),
    "억": Decimal(100_000_000),
}
_SCALED_PART = re.compile(
    rf"(?P<number>{BASE_NUMBER_SOURCE})\s*(?P<unit>{_KOREAN_UNIT_SOURCE})"
)


def _format_decimal(value: Decimal) -> str:
    normalized = format(value, "f")
    if "." in normalized:
        normalized = normalized.rstrip("0").rstrip(".")
    return normalized.removeprefix("+")


def normalize_numeric_literal(value: str) -> str:
    """`1만`, `3억 5천만`을 DB 숫자 리터럴과 비교 가능한 값으로 바꾼다."""
    compact = re.sub(r"\s+", "", value).replace(",", "")
    parts = list(_SCALED_PART.finditer(compact))
    if not parts or "".join(part.group(0) for part in parts) != compact:
        return compact.removeprefix("+")
    try:
        scaled = sum(
            (
                Decimal(part.group("number")) * _KOREAN_SCALE[part.group("unit")]
                for part in parts
            ),
            Decimal(0),
        )
    except InvalidOperation:
        return compact.removeprefix("+")
    return _format_decimal(scaled)


def normalized_numeric_literals(value: str) -> list[str]:
    return [
        normalize_numeric_literal(match.group("number"))
        for match in NUMERIC_LITERAL.finditer(value)
    ]
