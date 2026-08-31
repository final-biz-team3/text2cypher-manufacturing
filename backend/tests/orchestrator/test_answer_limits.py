"""generate_answer가 결과를 프롬프트에 넣기 전 적용할 전역 상한을 테스트한다."""

import copy
import json

import pytest

from orchestrator.nodes.answer_limits import (
    build_answer_context,
    truncate_result_for_answer,
)
from orchestrator.state import ComposedResult


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
    one_row_chars = len(json.dumps(rows[0], ensure_ascii=False, separators=(",", ":")))

    result = truncate_result_for_answer(
        rows, max_rows=10, max_chars=one_row_chars * 2 + 1
    )

    assert result["rows"] == rows[:2]
    assert result["total_count"] == 5
    assert result["truncated"] is True


def test_truncate_result_for_answer_rejects_first_row_over_char_budget() -> None:
    """첫 행도 문자 예산을 넘으면 포함하지 않아 전역 상한을 지킨다."""
    rows = [{"name": "x" * 100}, {"name": "y" * 100}]

    result = truncate_result_for_answer(rows, max_rows=10, max_chars=10)

    assert result["rows"] == []
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


def test_build_answer_context_preserves_joined_total_count_metadata() -> None:
    composed: ComposedResult = {
        "mode": "joined",
        "rows": [{"productId": 1}, {"productId": 2}],
        "sections": {},
        "error": None,
        "empty_reason": None,
        "total_count": 8,
        "truncated": True,
    }

    context = build_answer_context(composed, max_rows=1, max_chars=1000)

    assert context["rows"] == [{"productId": 1}]
    assert context["included_count"] == 1
    assert context["total_count"] == 8
    assert context["total_count_is_exact"] is True
    assert context["source_truncated"] is True
    assert context["prompt_truncated"] is True


def test_build_answer_context_marks_single_truncated_count_as_inexact() -> None:
    composed: ComposedResult = {
        "mode": "single",
        "rows": [{"productId": 1}, {"productId": 2}],
        "sections": {},
        "error": None,
        "empty_reason": None,
        "total_count": 2,
        "truncated": True,
    }

    context = build_answer_context(composed, max_rows=10, max_chars=1000)

    assert context["total_count"] == 2
    assert context["total_count_is_exact"] is False
    assert context["source_truncated"] is True


def test_build_answer_context_shares_budget_across_separate_sections() -> None:
    composed: ComposedResult = {
        "mode": "separate",
        "rows": [],
        "sections": {
            "processes": {
                "tool": "graph",
                "rows": [{"name": "절단"}, {"name": "용접"}],
                "empty_reason": None,
            },
            "parts": {
                "tool": "sql",
                "rows": [{"name": "프레임"}, {"name": "볼트"}],
                "empty_reason": None,
            },
        },
        "error": None,
        "empty_reason": None,
        "total_count": 4,
        "truncated": False,
    }

    context = build_answer_context(composed, max_rows=3, max_chars=1000)

    assert context["sections"]["processes"]["rows"] == [
        {"name": "절단"},
        {"name": "용접"},
    ]
    assert context["sections"]["parts"]["rows"] == [{"name": "프레임"}]
    assert context["included_count"] == 3
    assert context["prompt_truncated"] is True


def test_build_answer_context_does_not_mutate_composed_result() -> None:
    composed: ComposedResult = {
        "mode": "single",
        "rows": [{"productId": 1}, {"productId": 2}],
        "sections": {},
        "error": None,
        "empty_reason": None,
        "total_count": 2,
        "truncated": False,
    }
    original = copy.deepcopy(composed)

    build_answer_context(composed, max_rows=1, max_chars=1000)

    assert composed == original
