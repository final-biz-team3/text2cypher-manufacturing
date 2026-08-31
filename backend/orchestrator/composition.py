"""검증된 실행 계획과 source 결과를 결정적인 최종 데이터로 조합한다."""

from collections.abc import Mapping
from typing import Any, cast

from orchestrator.bom_shortage import calculate_bom_shortages
from orchestrator.planning import (
    BomShortageTransform,
    Subquery,
    validate_result_transform,
)
from orchestrator.state import (
    ComposedResult,
    ComposedSection,
    CompositionMode,
    EmptyReason,
    ToolName,
)

_EMPTY_REASONS = {"NO_DATA", "INCONCLUSIVE"}


def _failure(
    mode: CompositionMode,
    message: str,
    *,
    transform: str | None = None,
    truncated: bool = False,
) -> ComposedResult:
    result: ComposedResult = {
        "mode": mode,
        "rows": [],
        "sections": {},
        "error": message,
        "empty_reason": None,
        "total_count": 0,
        "truncated": truncated,
    }
    if transform is not None:
        result["transform"] = transform
    return result


def _source_rows(
    subquery: Subquery,
    tool_results: Mapping[str, dict[str, Any] | None],
    mode: CompositionMode,
) -> tuple[
    list[dict[str, Any]] | None,
    EmptyReason | None,
    bool,
    ComposedResult | None,
]:
    source = tool_results.get(subquery["tool"])
    label = f"{subquery['id']}({subquery['tool']})"
    if source is None:
        return None, None, False, _failure(mode, f"{label} 실행 결과가 없습니다.")
    if source.get("error") is not None:
        return (
            None,
            None,
            False,
            _failure(
                mode,
                f"{label} 실행 오류로 결과를 조합할 수 없습니다: {source['error']}",
            ),
        )

    rows = source.get("result")
    if rows is None:
        return None, None, False, _failure(mode, f"{label} result가 None입니다.")
    if not isinstance(rows, list):
        return None, None, False, _failure(mode, f"{label} result는 배열이어야 합니다.")
    for row_index, row in enumerate(rows):
        if not isinstance(row, dict):
            return (
                None,
                None,
                False,
                _failure(mode, f"{label}의 {row_index}번 행이 객체가 아닙니다."),
            )

    raw_truncated = source.get("truncated", False)
    if not isinstance(raw_truncated, bool):
        return (
            None,
            None,
            False,
            _failure(mode, f"{label} truncated는 bool이어야 합니다."),
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
            False,
            _failure(
                mode,
                f"{label} empty_reason이 지원되지 않습니다: {raw_empty_reason!r}",
            ),
        )
    if rows and empty_reason is not None:
        return (
            None,
            None,
            False,
            _failure(
                mode,
                f"{label}의 result가 비어 있지 않아 empty_reason을 지정할 수 없습니다.",
            ),
        )
    return (
        cast(list[dict[str, Any]], rows),
        empty_reason,
        raw_truncated,
        None,
    )


def _overall_empty_reason(
    sources: list[tuple[list[dict[str, Any]], EmptyReason | None, bool]],
) -> EmptyReason | None:
    if any(rows for rows, _, _ in sources):
        return None
    if any(reason == "INCONCLUSIVE" for _, reason, _ in sources):
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
    sources: list[tuple[list[dict[str, Any]], EmptyReason | None, bool]],
    *,
    row_limit: int,
) -> ComposedResult:
    mode: CompositionMode = "joined"
    left_rows, left_reason, left_truncated = sources[0]
    right_rows, right_reason, right_truncated = sources[1]
    if left_truncated or right_truncated:
        return _failure(
            mode,
            "source 결과가 행 상한을 초과해 완전한 join을 보장할 수 없습니다.",
            truncated=True,
        )
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


def _compose_bom_shortage(
    sources: list[tuple[list[dict[str, Any]], EmptyReason | None, bool]],
    transform: BomShortageTransform,
    *,
    row_limit: int,
) -> ComposedResult:
    mode: CompositionMode = "joined"
    graph_rows, graph_reason, graph_truncated = sources[0]
    sql_rows, sql_reason, sql_truncated = sources[1]
    transform_name = transform["type"]
    if graph_truncated or sql_truncated:
        return _failure(
            mode,
            "source 결과가 행 상한을 초과해 완전한 BOM 부족량을 계산할 수 없습니다.",
            transform=transform_name,
            truncated=True,
        )
    if not graph_rows and not sql_rows:
        empty_reason: EmptyReason = (
            "INCONCLUSIVE"
            if "INCONCLUSIVE" in (graph_reason, sql_reason)
            else "NO_DATA"
        )
        return {
            "mode": mode,
            "transform": transform_name,
            "rows": [],
            "sections": {},
            "error": None,
            "empty_reason": empty_reason,
            "total_count": 0,
            "truncated": False,
        }
    try:
        final_rows = calculate_bom_shortages(
            graph_rows,
            sql_rows,
            production_qty=transform["productionQty"],
        )
    except ValueError as exc:
        return _failure(mode, str(exc), transform=transform_name)
    stored_rows = final_rows[:row_limit]
    return {
        "mode": mode,
        "transform": transform_name,
        "rows": stored_rows,
        "sections": {},
        "error": None,
        "empty_reason": "NO_DATA" if not final_rows else None,
        "total_count": len(final_rows),
        "truncated": len(stored_rows) < len(final_rows),
    }


def compose_results(
    subqueries: list[Subquery],
    tool_results: Mapping[str, dict[str, Any] | None],
    *,
    row_limit: int,
    result_transform: BomShortageTransform | None = None,
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
        transform_name = (
            result_transform["type"] if result_transform is not None else None
        )
        return _failure(
            mode,
            "결과 행 상한은 0 이상이어야 합니다.",
            transform=transform_name,
        )

    try:
        validated_transform = validate_result_transform(result_transform, subqueries)
    except ValueError as exc:
        return _failure(mode, str(exc), transform="bom_shortage_v1")

    sources: list[tuple[list[dict[str, Any]], EmptyReason | None, bool]] = []
    for subquery in subqueries:
        rows, empty_reason, truncated, failure = _source_rows(
            subquery, tool_results, mode
        )
        if failure is not None:
            if validated_transform is not None:
                failure["transform"] = validated_transform["type"]
            return failure
        assert rows is not None
        sources.append((rows, empty_reason, truncated))

    if validated_transform is not None:
        return _compose_bom_shortage(
            sources,
            validated_transform,
            row_limit=row_limit,
        )

    if mode == "single":
        rows, empty_reason, truncated = sources[0]
        return {
            "mode": mode,
            "rows": rows,
            "sections": {},
            "error": None,
            "empty_reason": (
                empty_reason if not rows and empty_reason is not None else None
            ),
            "total_count": len(rows),
            "truncated": truncated,
        }

    if mode == "separate":
        sections: dict[str, ComposedSection] = {}
        for subquery, (rows, empty_reason, _) in zip(subqueries, sources, strict=True):
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
            "total_count": sum(len(rows) for rows, _, _ in sources),
            "truncated": any(truncated for _, _, truncated in sources),
        }

    return _compose_joined(subqueries, sources, row_limit=row_limit)
