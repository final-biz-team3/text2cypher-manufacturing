"""SQL/Cypher 결과를 계약 필드로 정규화하고 안정적인 hash를 계산한다."""

import hashlib
import json
import re
from datetime import date, datetime
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
from functools import cmp_to_key
from typing import Any

from evaluation.errors import ResultContractError

_DECIMAL_QUANTUM = Decimal("0.000001")
_ORDERING = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s+(ASC|DESC)$", re.IGNORECASE)
_NUMBER = re.compile(r"^-?\d+(?:\.\d+)?$")


def _normalize_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, (Decimal, float)):
        try:
            decimal_value = Decimal(str(value)).quantize(
                _DECIMAL_QUANTUM, rounding=ROUND_HALF_EVEN
            )
        except (InvalidOperation, ValueError) as exc:
            raise ResultContractError(
                f"숫자를 정규화할 수 없습니다: {value!r}"
            ) from exc
        return format(decimal_value, ".6f")
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, (list, tuple)):
        return [_normalize_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _normalize_value(item) for key, item in value.items()}
    iso_format = getattr(value, "iso_format", None)
    if callable(iso_format):
        return iso_format()
    isoformat = getattr(value, "isoformat", None)
    if callable(isoformat):
        return isoformat()
    raise ResultContractError(f"지원하지 않는 결과 타입입니다: {type(value).__name__}")


def _field_value(
    row: dict[str, Any],
    field: str,
    aliases: tuple[str, ...],
    *,
    allow_single_column_fallback: bool,
) -> Any:
    def canonical_name(value: str) -> str:
        return re.sub(r"[^\w]", "", value, flags=re.UNICODE).replace("_", "").casefold()

    accepted = {canonical_name(name) for name in (field, *aliases)}
    matches = [
        (key, value) for key, value in row.items() if canonical_name(key) in accepted
    ]
    if not matches:
        # 단일 출력 계약은 어떤 컬럼을 뜻하는지 위치 추측이 필요 없다. 값은
        # 이어지는 Gold hash 비교로 검증하므로 의미가 분명한 임의 alias 때문에
        # 올바른 집계 쿼리를 미평가하지 않는다.
        if allow_single_column_fallback and len(row) == 1:
            return _normalize_value(next(iter(row.values())))
        raise ResultContractError(f"필수 결과 필드 {field!r}가 없습니다.")
    normalized = [_normalize_value(value) for _, value in matches]
    if any(value != normalized[0] for value in normalized[1:]):
        names = ", ".join(key for key, _ in matches)
        raise ResultContractError(f"alias 필드 값이 서로 다릅니다: {names}")
    return normalized[0]


def _comparable(value: Any) -> Any:
    if isinstance(value, int) and not isinstance(value, bool):
        return (0, Decimal(value))
    if isinstance(value, str) and _NUMBER.fullmatch(value):
        return (0, Decimal(value))
    if isinstance(value, list):
        return (1, tuple(_comparable(item) for item in value))
    return (2, json.dumps(value, ensure_ascii=False, sort_keys=True))


def _sort_rows(rows: list[dict[str, Any]], ordering: tuple[str, ...]) -> None:
    parsed: list[tuple[str, bool]] = []
    for expression in ordering:
        match = _ORDERING.fullmatch(expression.strip())
        if match is None:
            raise ResultContractError(
                f"지원하지 않는 ordering 표현입니다: {expression}"
            )
        parsed.append((match.group(1), match.group(2).upper() == "DESC"))

    def compare(left: dict[str, Any], right: dict[str, Any]) -> int:
        for field, descending in parsed:
            left_value = left.get(field)
            right_value = right.get(field)
            if left_value is None and right_value is None:
                continue
            if left_value is None:
                return 1
            if right_value is None:
                return -1
            left_key = _comparable(left_value)
            right_key = _comparable(right_value)
            if left_key == right_key:
                continue
            result = -1 if left_key < right_key else 1
            return -result if descending else result
        return 0

    rows.sort(key=cmp_to_key(compare))


def normalize_rows(
    rows: list[dict[str, Any]],
    *,
    required_outputs: tuple[str, ...],
    aliases: dict[str, tuple[str, ...]],
    ordering: tuple[str, ...],
) -> list[dict[str, Any]]:
    """허용 alias를 계약 필드로 바꾸고 타입·행 순서를 정규화한다."""
    normalized = [
        {
            field: _field_value(
                row,
                field,
                aliases.get(field, ()),
                allow_single_column_fallback=len(required_outputs) == 1,
            )
            for field in required_outputs
        }
        for row in rows
    ]
    _sort_rows(normalized, ordering)
    return normalized


def normalized_sha256(rows: list[dict[str, Any]]) -> str:
    payload = json.dumps(
        rows,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
