"""Result contract shared by production SQL and Cypher executors."""

from collections.abc import Callable, Sequence
from typing import Any, TypedDict


class QueryResultBatch(TypedDict):
    rows: list[dict[str, Any]]
    truncated: bool


def make_batch(
    items: Sequence[Any],
    row_limit: int,
    *,
    extract: Callable[[Any], dict[str, Any]] = lambda item: item,
) -> QueryResultBatch:
    """row_limit+1개를 가져온 결과에서 row_limit개로 자르고 truncated 여부를
    계산한다. SQL(dict_row, 그대로 씀)과 Cypher(Record, .data()로 변환)
    실행부가 각자 손으로 구현하던 동일한 "N+1개 가져와서 자르기" 로직을
    공유한다."""
    return {
        "rows": [extract(item) for item in items[:row_limit]],
        "truncated": len(items) > row_limit,
    }
