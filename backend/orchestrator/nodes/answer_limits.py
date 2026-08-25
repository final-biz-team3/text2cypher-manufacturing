"""generate_answer가 결과를 프롬프트에 넣기 전 적용할 행 수·문자 수 상한.

쿼리에 LIMIT을 넣지 않고 실행 후 이 단계에서만 자르며, 실제 총 건수는
별도로 보존해 "총 N건 중 상위 M건" 형태로 답변에 명시할 수 있게 한다.
기본값은 실측이 아니라 확정 질의셋 분석 기반 추정치이며, 그 질의셋 범위를
벗어나는 질의로 성능을 검증할 때 재측정해 조정해야 한다."""

import os
from typing import Any, TypedDict

DEFAULT_MAX_ROWS = 200
DEFAULT_MAX_CHARS = 8000


class TruncatedResult(TypedDict):
    rows: list[Any]
    total_count: int
    truncated: bool


def truncate_result_for_answer(
    rows: list[Any],
    *,
    max_rows: int | None = None,
    max_chars: int | None = None,
) -> TruncatedResult:
    """rows를 max_rows/max_chars 중 먼저 걸리는 기준으로 자른다. 생략하면
    ANSWER_MAX_ROWS/ANSWER_MAX_CHARS 환경변수(없으면 기본값)를 쓴다. 호출 전에
    각 질의의 ordering 기준으로 이미 정렬돼 있어야 한다."""
    if max_rows is None:
        max_rows = int(os.getenv("ANSWER_MAX_ROWS", str(DEFAULT_MAX_ROWS)))
    if max_chars is None:
        max_chars = int(os.getenv("ANSWER_MAX_CHARS", str(DEFAULT_MAX_CHARS)))

    total_count = len(rows)
    truncated_rows: list[Any] = []
    char_count = 0
    for row in rows[:max_rows]:
        row_chars = len(str(row))
        if truncated_rows and char_count + row_chars > max_chars:
            break
        truncated_rows.append(row)
        char_count += row_chars

    return {
        "rows": truncated_rows,
        "total_count": total_count,
        "truncated": len(truncated_rows) < total_count,
    }
