"""검증된 실행 계획과 source 결과를 결정적인 최종 데이터로 조합한다."""

from collections.abc import Mapping
from typing import Any, cast

from orchestrator.planning import Subquery
from orchestrator.state import (
    ComposedResult,
    ComposedSection,
    CompositionMode,
    EmptyReason,
    ToolName,
)

_EMPTY_REASONS = {"NO_DATA", "INCONCLUSIVE"}


def _failure(mode: CompositionMode, message: str) -> ComposedResult:
    return {
        "mode": mode,
        "rows": [],
        "sections": {},
        "error": message,
        "empty_reason": None,
        "total_count": 0,
        "truncated": False,
    }


def _source_rows(
    subquery: Subquery,
    tool_results: Mapping[str, dict[str, Any] | None],
    mode: CompositionMode,
) -> tuple[list[dict[str, Any]] | None, EmptyReason | None, ComposedResult | None]:
    source = tool_results.get(subquery["tool"])
    label = f"{subquery['id']}({subquery['tool']})"
    if source is None:
        return None, None, _failure(mode, f"{label} 실행 결과가 없습니다.")
    if source.get("error") is not None:
        return (
            None,
            None,
            _failure(
                mode,
                f"{label} 실행 오류로 결과를 조합할 수 없습니다: {source['error']}",
            ),
        )

    rows = source.get("result")
    if rows is None:
        return None, None, _failure(mode, f"{label} result가 None입니다.")
    if not isinstance(rows, list):
        return None, None, _failure(mode, f"{label} result는 배열이어야 합니다.")
    for row_index, row in enumerate(rows):
        if not isinstance(row, dict):
            return (
                None,
                None,
                _failure(mode, f"{label}의 {row_index}번 행이 객체가 아닙니다."),
            )

    raw_empty_reason = source.get("empty_reason")
    if raw_empty_reason is None:
        empty_reason = None
    elif isinstance(raw_empty_reason, str) and raw_empty_reason in _EMPTY_REASONS:
        empty_reason = cast(EmptyReason, raw_empty_reason)
    else:
        return (
            None,
            None,
            _failure(
                mode,
                f"{label} empty_reason이 지원되지 않습니다: {raw_empty_reason!r}",
            ),
        )
    return (
        cast(list[dict[str, Any]], rows),
        empty_reason,
        None,
    )


def _overall_empty_reason(
    rows_and_reasons: list[tuple[list[dict[str, Any]], EmptyReason | None]],
) -> EmptyReason | None:
    if any(rows for rows, _ in rows_and_reasons):
        return None
    if any(reason == "INCONCLUSIVE" for _, reason in rows_and_reasons):
        return "INCONCLUSIVE"
    return "NO_DATA"


def _row_key(
    row: Any,
    *,
    join_keys: list[str],
    subquery: Subquery,
    row_index: int,
) -> tuple[tuple[Any, ...] | None, str | None]:
    label = f"{subquery['id']}({subquery['tool']}) {row_index}번 행"
    if not isinstance(row, dict):
        return None, f"{label}이 객체가 아닙니다."

    values: list[Any] = []
    for alias in join_keys:
        if alias not in row:
            return None, f"{label}에 join key {alias!r}가 없습니다."
        value = row[alias]
        if value is None:
            return None, f"{label}의 join key {alias!r} 값이 None입니다."
        values.append(value)

    key = tuple(values)
    try:
        hash(key)
    except TypeError:
        return None, f"{label}의 join key 값은 hashable해야 합니다: {key!r}"
    return key, None


def _merge_rows(
    left: dict[str, Any],
    right: dict[str, Any],
    *,
    join_keys: set[str],
) -> tuple[dict[str, Any] | None, str | None]:
    merged = dict(left)
    for field, value in right.items():
        if field in join_keys:
            continue
        if field in merged and merged[field] != value:
            return None, (
                f"비-key 필드 {field!r}의 값이 충돌합니다: "
                f"{merged[field]!r} != {value!r}"
            )
        if field not in merged:
            merged[field] = value
    return merged, None


def _compose_joined(
    subqueries: list[Subquery],
    sources: list[tuple[list[dict[str, Any]], EmptyReason | None]],
    *,
    row_limit: int,
) -> ComposedResult:
    mode: CompositionMode = "joined"
    left_rows, left_reason = sources[0]
    right_rows, right_reason = sources[1]
    if not left_rows or not right_rows:
        empty_reason: EmptyReason = (
            "INCONCLUSIVE"
            if "INCONCLUSIVE" in (left_reason, right_reason)
            else "NO_DATA"
        )
        return {
            "mode": mode,
            "rows": [],
            "sections": {},
            "error": None,
            "empty_reason": empty_reason,
            "total_count": 0,
            "truncated": False,
        }

    join_keys = subqueries[0]["joinKeys"]
    left_keyed: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
    left_domain: set[tuple[Any, ...]] = set()
    for index, row in enumerate(left_rows):
        key, error = _row_key(
            row,
            join_keys=join_keys,
            subquery=subqueries[0],
            row_index=index,
        )
        if error is not None:
            return _failure(mode, error)
        assert key is not None
        left_keyed.append((key, row))
        left_domain.add(key)

    right_by_key: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    for index, row in enumerate(right_rows):
        key, error = _row_key(
            row,
            join_keys=join_keys,
            subquery=subqueries[1],
            row_index=index,
        )
        if error is not None:
            return _failure(mode, error)
        assert key is not None
        if key not in left_domain:
            return _failure(
                mode,
                f"{subqueries[1]['id']}({subqueries[1]['tool']})의 join key "
                f"{key!r}가 선행 결과의 바인딩 범위를 벗어났습니다.",
            )
        right_by_key.setdefault(key, []).append(row)

    rows: list[dict[str, Any]] = []
    total_count = 0
    join_key_set = set(join_keys)
    for key, left_row in left_keyed:
        for right_row in right_by_key.get(key, []):
            merged, error = _merge_rows(
                left_row,
                right_row,
                join_keys=join_key_set,
            )
            if error is not None:
                return _failure(mode, error)
            assert merged is not None
            total_count += 1
            if len(rows) < row_limit:
                rows.append(merged)

    return {
        "mode": mode,
        "rows": rows,
        "sections": {},
        "error": None,
        "empty_reason": "NO_DATA" if total_count == 0 else None,
        "total_count": total_count,
        "truncated": total_count > len(rows),
    }


def compose_results(
    subqueries: list[Subquery],
    tool_results: Mapping[str, dict[str, Any] | None],
    *,
    row_limit: int,
) -> ComposedResult:
    """실행 계획 순서와 join 계약에 따라 source 결과를 조합한다.

    입력을 변경하지 않으며 DB·환경변수에 접근하지 않는 순수 함수다.
    """
    if not subqueries:
        return _failure("single", "실행 계획에 subquery가 없습니다.")
    if len(subqueries) > 2:
        return _failure("separate", "두 개를 넘는 subquery는 지원하지 않습니다.")

    if len(subqueries) == 1:
        mode: CompositionMode = "single"
    elif not subqueries[0]["joinKeys"] and not subqueries[1]["joinKeys"]:
        mode = "separate"
    elif (
        subqueries[0]["joinKeys"]
        and subqueries[1]["joinKeys"]
        and set(subqueries[0]["joinKeys"]) == set(subqueries[1]["joinKeys"])
    ):
        mode = "joined"
    else:
        return _failure("joined", "HYBRID joinKeys 계약이 일치하지 않습니다.")
    if row_limit < 0:
        return _failure(mode, "결과 행 상한은 0 이상이어야 합니다.")

    sources: list[tuple[list[dict[str, Any]], EmptyReason | None]] = []
    for subquery in subqueries:
        rows, empty_reason, failure = _source_rows(subquery, tool_results, mode)
        if failure is not None:
            return failure
        assert rows is not None
        sources.append((rows, empty_reason))

    if mode == "single":
        rows, empty_reason = sources[0]
        return {
            "mode": mode,
            "rows": rows,
            "sections": {},
            "error": None,
            "empty_reason": (
                empty_reason if not rows and empty_reason is not None else None
            ),
            "total_count": len(rows),
            "truncated": False,
        }

    if mode == "separate":
        sections: dict[str, ComposedSection] = {}
        for subquery, (rows, empty_reason) in zip(subqueries, sources, strict=True):
            sections[subquery["id"]] = {
                "tool": cast(ToolName, subquery["tool"]),
                "rows": rows,
                "empty_reason": empty_reason if not rows else None,
            }
        return {
            "mode": mode,
            "rows": [],
            "sections": sections,
            "error": None,
            "empty_reason": _overall_empty_reason(sources),
            "total_count": sum(len(rows) for rows, _ in sources),
            "truncated": False,
        }

    return _compose_joined(subqueries, sources, row_limit=row_limit)
