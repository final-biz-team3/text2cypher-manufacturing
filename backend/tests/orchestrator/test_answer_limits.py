"""generate_answer가 결과를 프롬프트에 넣기 전 적용할 행 수·문자 수 상한을 테스트한다."""

import pytest

from orchestrator.nodes.answer_limits import truncate_result_for_answer


def test_truncate_result_for_answer_keeps_all_rows_when_under_both_limits() -> None:
    """행 수·문자 수 모두 상한 미만이면 자르지 않는다."""
    rows = [{"productId": 1}, {"productId": 2}]

    result = truncate_result_for_answer(rows, max_rows=10, max_chars=1000)

    assert result["rows"] == rows
    assert result["total_count"] == 2
    assert result["truncated"] is False


def test_truncate_result_for_answer_cuts_by_row_limit() -> None:
    """행 수 상한을 넘으면 앞에서부터 상한만큼만 남긴다."""
    rows = [{"productId": i} for i in range(5)]

    result = truncate_result_for_answer(rows, max_rows=3, max_chars=100_000)

    assert result["rows"] == rows[:3]
    assert result["total_count"] == 5
    assert result["truncated"] is True


def test_truncate_result_for_answer_cuts_by_char_limit_before_row_limit() -> None:
    """행 수 상한 이내라도 누적 문자 수가 상한을 넘으면 그 전에서 자른다."""
    rows = [{"name": "x" * 40} for _ in range(5)]
    one_row_chars = len(str(rows[0]))

    result = truncate_result_for_answer(
        rows, max_rows=10, max_chars=one_row_chars * 2 + 1
    )

    assert result["rows"] == rows[:2]
    assert result["total_count"] == 5
    assert result["truncated"] is True


def test_truncate_result_for_answer_always_keeps_at_least_one_row() -> None:
    """첫 행 하나만으로 문자 수 상한을 넘어도 결과가 비지 않도록 최소 1건은 남긴다."""
    rows = [{"name": "x" * 100}, {"name": "y" * 100}]

    result = truncate_result_for_answer(rows, max_rows=10, max_chars=10)

    assert result["rows"] == rows[:1]
    assert result["total_count"] == 2
    assert result["truncated"] is True


def test_truncate_result_for_answer_handles_empty_list() -> None:
    """빈 결과는 그대로 빈 결과를 반환하고 truncated는 False다."""
    result = truncate_result_for_answer([], max_rows=10, max_chars=100)

    assert result["rows"] == []
    assert result["total_count"] == 0
    assert result["truncated"] is False


def test_truncate_result_for_answer_uses_env_configured_defaults_when_omitted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """max_rows/max_chars를 생략하면 ANSWER_MAX_ROWS/ANSWER_MAX_CHARS 환경변수(또는
    기본값)를 사용한다."""
    monkeypatch.setenv("ANSWER_MAX_ROWS", "2")
    rows = [{"productId": i} for i in range(5)]

    result = truncate_result_for_answer(rows)

    assert result["rows"] == rows[:2]
    assert result["truncated"] is True
