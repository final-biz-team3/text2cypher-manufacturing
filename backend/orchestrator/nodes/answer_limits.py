"""generate_answer가 결과를 프롬프트에 넣기 전 적용할 행 수·문자 수 상한.

실행 결과를 그대로 프롬프트에 넣으면 컨텍스트 초과·비용 낭비가 생길 수 있어
행 수·문자 수 상한 중 먼저 걸리는 쪽으로 자른다. 쿼리 자체에 LIMIT을 넣지
않고 실행 후 이 단계에서만 자르며, 실제 총 건수는 별도로 보존해 generate_answer가
"총 N건 중 상위 M건" 형태로 답변에 명시할 수 있게 한다.

상한 기본값은 실측 대신 queries/query_contracts.json의 확정 질의셋
20개(RQ01~RQ20) 분석으로 정했다(2026-08-25). 행 수는 계약이 BOM 다단계
질의(RQ12~14,17,18)에 이미 허용한 최댓값(200)을 그대로 채택했다. 문자 수는
그 200행 케이스(경로 배열 포함 행당 약 300~400자 추정)를 기준으로, 나머지
15개의 작은/집계 질의는 전혀 안 걸리면서 BOM 다단계 질의는 실제로 걸리도록
잡았다. 20개 이외의 질의로 성능을 검증할 때 재측정해 조정한다
(tests/orchestrator/test_graph_integration.py가 RQ01·02·08·12·13에 대해
실제 OpenAI+PostgreSQL로 쿼리 생성까지는 이미 검증하고 있어, 여기에 DB
실행·크기 측정을 추가하는 방향으로 확장할 수 있다)."""

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
