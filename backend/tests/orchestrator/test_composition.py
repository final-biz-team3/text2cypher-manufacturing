"""실행 결과의 단일·결합·분리 조합 계약을 테스트한다."""

from copy import deepcopy
from typing import Any

import pytest

from orchestrator.composition import compose_results
from orchestrator.planning import Subquery


def _step(
    subquery_id: str,
    tool: str,
    *,
    join_keys: list[str] | None = None,
) -> Subquery:
    keys = join_keys or []
    return {
        "id": subquery_id,
        "tool": tool,
        "question": "질문",
        "dependsOn": [],
        "requiredOutputs": keys,
        "joinKeys": keys,
    }


def _source(
    rows: list[Any] | None,
    *,
    error: str | None = None,
    empty_reason: str | None = None,
) -> dict[str, Any]:
    return {
        "result": rows,
        "error": error,
        "attempts": [],
        "empty_reason": empty_reason,
    }


def _joined(
    left_rows: list[Any] | None,
    right_rows: list[Any] | None,
    *,
    left_keys: list[str] | None = None,
    right_keys: list[str] | None = None,
    row_limit: int = 200,
    left_error: str | None = None,
    right_error: str | None = None,
    left_empty_reason: str | None = None,
    right_empty_reason: str | None = None,
):
    canonical_keys = left_keys or ["id"]
    return compose_results(
        [
            _step("graph_base", "graph", join_keys=canonical_keys),
            _step(
                "sql_followup",
                "sql",
                join_keys=right_keys or canonical_keys,
            ),
        ],
        {
            "graph": _source(
                left_rows,
                error=left_error,
                empty_reason=left_empty_reason,
            ),
            "sql": _source(
                right_rows,
                error=right_error,
                empty_reason=right_empty_reason,
            ),
        },
        row_limit=row_limit,
    )


@pytest.mark.parametrize("tool", ["sql", "graph"])
def test_single_result_is_normalized_without_changing_rows(tool: str) -> None:
    rows = [{"value": 1}, {"value": 1}]

    result = compose_results(
        [_step("only", tool, join_keys=["value"])],
        {tool: _source(rows)},
        row_limit=200,
    )

    assert result == {
        "mode": "single",
        "rows": rows,
        "sections": {},
        "error": None,
        "empty_reason": None,
        "total_count": 2,
        "truncated": False,
    }


def test_legacy_hybrid_is_kept_in_plan_order_without_cartesian_product() -> None:
    result = compose_results(
        [_step("sql_facts", "sql"), _step("graph_paths", "graph")],
        {
            "sql": _source([{"fact": 1}]),
            "graph": _source([], empty_reason="NO_DATA"),
        },
        row_limit=200,
    )

    assert result["mode"] == "separate"
    assert result["rows"] == []
    assert list(result["sections"]) == ["sql_facts", "graph_paths"]
    assert result["sections"] == {
        "sql_facts": {
            "tool": "sql",
            "rows": [{"fact": 1}],
            "empty_reason": None,
        },
        "graph_paths": {
            "tool": "graph",
            "rows": [],
            "empty_reason": "NO_DATA",
        },
    }
    assert result["empty_reason"] is None
    assert result["total_count"] == 1


def test_join_preserves_left_duplicates_for_many_to_one() -> None:
    result = _joined(
        [{"id": 7, "path": "a"}, {"id": 7, "path": "b"}],
        [{"id": 7, "stock": 30}],
    )

    assert result["rows"] == [
        {"id": 7, "path": "a", "stock": 30},
        {"id": 7, "path": "b", "stock": 30},
    ]


def test_join_preserves_right_order_for_one_to_many() -> None:
    result = _joined(
        [{"id": 7, "fact": "base"}],
        [
            {"id": 7, "sequence": 20},
            {"id": 7, "sequence": 10},
        ],
    )

    assert [row["sequence"] for row in result["rows"]] == [20, 10]


def test_join_uses_first_subquery_key_order_and_multiple_keys() -> None:
    result = _joined(
        [
            {"componentId": 7, "supplierId": 2, "path": "a"},
            {"componentId": 8, "supplierId": 2, "path": "b"},
        ],
        [
            {"supplierId": 2, "componentId": 8, "stock": 80},
            {"supplierId": 2, "componentId": 7, "stock": 70},
        ],
        left_keys=["componentId", "supplierId"],
        right_keys=["supplierId", "componentId"],
    )

    assert result["rows"] == [
        {
            "componentId": 7,
            "supplierId": 2,
            "path": "a",
            "stock": 70,
        },
        {
            "componentId": 8,
            "supplierId": 2,
            "path": "b",
            "stock": 80,
        },
    ]


def test_join_excludes_left_rows_without_a_right_match() -> None:
    result = _joined(
        [{"id": 1, "name": "one"}, {"id": 2, "name": "two"}],
        [{"id": 2, "stock": 20}],
    )

    assert result["rows"] == [{"id": 2, "name": "two", "stock": 20}]


def test_join_rejects_followup_key_outside_left_binding_domain() -> None:
    result = _joined(
        [{"id": 1}],
        [{"id": 1, "stock": 10}, {"id": 999, "stock": 20}],
        row_limit=1,
    )

    assert result["rows"] == []
    assert "바인딩 범위를 벗어났습니다" in str(result["error"])


@pytest.mark.parametrize(
    ("rows", "message"),
    [
        ([{"id": None}], "None"),
        ([{"id": [1, 2]}], "hashable"),
        ([{"id": {"nested": 1}}], "hashable"),
        ([{"other": 1}], "join key 'id'가 없습니다"),
        (["not-a-dict"], "객체가 아닙니다"),
    ],
)
def test_join_rejects_invalid_key_rows(rows: list[Any], message: str) -> None:
    result = _joined(rows, [{"id": 1}])

    assert result["rows"] == []
    assert message in str(result["error"])


def test_join_keeps_equal_overlapping_non_key_field_once() -> None:
    result = _joined(
        [{"id": 1, "name": "same"}],
        [{"id": 1, "name": "same", "stock": 10}],
    )

    assert result["rows"] == [{"id": 1, "name": "same", "stock": 10}]


def test_join_rejects_conflicting_field_even_after_row_limit() -> None:
    result = _joined(
        [{"id": 1, "name": "left"}],
        [
            {"id": 1, "name": "left", "stock": 10},
            {"id": 1, "name": "right", "stock": 20},
        ],
        row_limit=1,
    )

    assert result["rows"] == []
    assert "필드 'name'의 값이 충돌" in str(result["error"])


def test_join_source_error_fails_without_mutating_original_results() -> None:
    left = _source([{"id": 1}])
    right = _source(None, error="database down")
    originals = deepcopy((left, right))

    result = compose_results(
        [
            _step("graph_base", "graph", join_keys=["id"]),
            _step("sql_followup", "sql", join_keys=["id"]),
        ],
        {"graph": left, "sql": right},
        row_limit=200,
    )

    assert result["error"] is not None
    assert result["rows"] == []
    assert (left, right) == originals


@pytest.mark.parametrize(
    ("left_reason", "right_reason", "expected"),
    [
        ("NO_DATA", "NO_DATA", "NO_DATA"),
        ("INCONCLUSIVE", "NO_DATA", "INCONCLUSIVE"),
        ("NO_DATA", "INCONCLUSIVE", "INCONCLUSIVE"),
    ],
)
def test_join_empty_result_reason_priority(
    left_reason: str, right_reason: str, expected: str
) -> None:
    result = _joined(
        [],
        [],
        left_empty_reason=left_reason,
        right_empty_reason=right_reason,
    )

    assert result["empty_reason"] == expected
    assert result["total_count"] == 0
    assert result["truncated"] is False


def test_separate_empty_sections_use_inconclusive_priority() -> None:
    result = compose_results(
        [_step("sql_facts", "sql"), _step("graph_paths", "graph")],
        {
            "sql": _source([], empty_reason="NO_DATA"),
            "graph": _source([], empty_reason="INCONCLUSIVE"),
        },
        row_limit=200,
    )

    assert result["empty_reason"] == "INCONCLUSIVE"
    assert result["sections"]["sql_facts"]["empty_reason"] == "NO_DATA"
    assert result["sections"]["graph_paths"]["empty_reason"] == "INCONCLUSIVE"


def test_join_counts_all_rows_and_truncates_only_stored_rows() -> None:
    result = _joined(
        [{"id": 1, "path": "a"}, {"id": 1, "path": "b"}],
        [
            {"id": 1, "operation": 10},
            {"id": 1, "operation": 20},
        ],
        row_limit=3,
    )

    assert result["rows"] == [
        {"id": 1, "path": "a", "operation": 10},
        {"id": 1, "path": "a", "operation": 20},
        {"id": 1, "path": "b", "operation": 10},
    ]
    assert result["total_count"] == 4
    assert result["truncated"] is True
