"""LLM 답변 컨텍스트에 적용할 전역 행 수·문자 수 상한."""

import json
import os
from collections.abc import Mapping
from typing import Any, TypedDict

from orchestrator.state import ComposedResult

DEFAULT_MAX_ROWS = 200
DEFAULT_MAX_CHARS = 8000


class TruncatedResult(TypedDict):
    rows: list[Any]
    total_count: int
    truncated: bool


class AnswerSectionContext(TypedDict):
    tool: str
    rows: list[dict[str, Any]]
    empty_reason: str | None
    included_count: int
    known_count: int
    count_is_exact: bool


class AnswerContext(TypedDict):
    mode: str
    transform: str | None
    rows: list[dict[str, Any]]
    sections: dict[str, AnswerSectionContext]
    empty_reason: str | None
    included_count: int
    total_count: int
    total_count_is_exact: bool
    source_truncated: bool
    prompt_truncated: bool


def _json_size(value: Any) -> int:
    """OpenAI 프롬프트에 들어가는 JSON 표현과 같은 기준으로 문자 수를 센다."""
    return len(
        json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        )
    )


def _configured_limit(name: str, default: int, explicit: int | None) -> int:
    value = int(os.getenv(name, str(default))) if explicit is None else explicit
    if value < 0:
        raise ValueError(f"{name} must be zero or greater.")
    return value


def truncate_result_for_answer(
    rows: list[Any],
    *,
    max_rows: int | None = None,
    max_chars: int | None = None,
) -> TruncatedResult:
    """정렬된 rows의 prefix를 JSON 문자 수와 행 수 상한 안에서 보존한다."""
    row_limit = _configured_limit("ANSWER_MAX_ROWS", DEFAULT_MAX_ROWS, max_rows)
    char_limit = _configured_limit("ANSWER_MAX_CHARS", DEFAULT_MAX_CHARS, max_chars)

    total_count = len(rows)
    truncated_rows: list[Any] = []
    char_count = 0
    for row in rows[:row_limit]:
        row_chars = _json_size(row)
        if char_count + row_chars > char_limit:
            break
        truncated_rows.append(row)
        char_count += row_chars

    return {
        "rows": truncated_rows,
        "total_count": total_count,
        "truncated": len(truncated_rows) < total_count,
    }


def _truncate_sections(
    sections: Mapping[str, Any],
    *,
    max_rows: int,
    max_chars: int,
) -> tuple[dict[str, list[dict[str, Any]]], int, bool]:
    """분리 결과를 round-robin으로 담아 한 섹션의 예산 독점을 막는다."""
    included: dict[str, list[dict[str, Any]]] = {
        section_id: [] for section_id in sections
    }
    positions = {section_id: 0 for section_id in sections}
    blocked: set[str] = set()
    included_count = 0
    char_count = 0

    while included_count < max_rows:
        progressed = False
        for section_id, section in sections.items():
            if included_count >= max_rows or section_id in blocked:
                continue
            rows = section.get("rows", [])
            position = positions[section_id]
            if position >= len(rows):
                blocked.add(section_id)
                continue

            row = rows[position]
            row_chars = _json_size(row)
            if char_count + row_chars > max_chars:
                blocked.add(section_id)
                continue

            included[section_id].append(row)
            positions[section_id] += 1
            included_count += 1
            char_count += row_chars
            progressed = True

        if not progressed:
            break

    source_count = sum(len(section.get("rows", [])) for section in sections.values())
    return included, included_count, included_count < source_count


def build_answer_context(
    composed_result: ComposedResult,
    *,
    max_rows: int | None = None,
    max_chars: int | None = None,
) -> AnswerContext:
    """composed_result만으로 LLM에 전달할 JSON 안전 컨텍스트를 만든다.

    single/joined는 rows를, separate는 모든 section을 하나의 전역 예산으로
    제한한다. 원본 dict와 row 객체는 변경하지 않는다.
    """
    row_limit = _configured_limit("ANSWER_MAX_ROWS", DEFAULT_MAX_ROWS, max_rows)
    char_limit = _configured_limit("ANSWER_MAX_CHARS", DEFAULT_MAX_CHARS, max_chars)
    mode = composed_result["mode"]
    source_truncated = composed_result["truncated"]
    sections: dict[str, AnswerSectionContext] = {}

    if mode == "separate":
        source_sections = composed_result["sections"]
        included_by_section, included_count, prompt_truncated = _truncate_sections(
            source_sections,
            max_rows=row_limit,
            max_chars=char_limit,
        )
        for section_id, section in source_sections.items():
            source_rows = section["rows"]
            included_rows = included_by_section[section_id]
            sections[section_id] = {
                "tool": section["tool"],
                "rows": included_rows,
                "empty_reason": section["empty_reason"],
                "included_count": len(included_rows),
                "known_count": len(source_rows),
                "count_is_exact": not source_truncated,
            }
        rows: list[dict[str, Any]] = []
    else:
        limited = truncate_result_for_answer(
            composed_result["rows"],
            max_rows=row_limit,
            max_chars=char_limit,
        )
        rows = limited["rows"]
        included_count = len(rows)
        prompt_truncated = limited["truncated"]

    # joined(파생 BOM 포함)는 source가 완전할 때만 조합되므로 total_count가
    # 정확하다. single/separate의 truncated는 DB fetch 상한을 뜻해 실제 총계가
    # 더 클 수 있다.
    total_count_is_exact = mode == "joined" or not source_truncated
    return {
        "mode": mode,
        "transform": composed_result.get("transform"),
        "rows": rows,
        "sections": sections,
        "empty_reason": composed_result["empty_reason"],
        "included_count": included_count,
        "total_count": composed_result["total_count"],
        "total_count_is_exact": total_count_is_exact,
        "source_truncated": source_truncated,
        "prompt_truncated": prompt_truncated,
    }
