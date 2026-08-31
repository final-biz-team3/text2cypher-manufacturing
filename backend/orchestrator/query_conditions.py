"""자연어 질문의 명시적 숫자 필터 보존을 위한 결정적 검사."""

import re
from typing import Literal, NamedTuple

from orchestrator.numeric_literals import (
    BASE_NUMBER_SOURCE,
    NUMERIC_LITERAL,
    NUMERIC_LITERAL_SOURCE,
    normalize_numeric_literal,
)

ConstraintOperator = Literal["eq", "gte", "lte", "gt", "lt", "limit", "present"]


class NumericConstraint(NamedTuple):
    value: str
    operator: ConstraintOperator


_COMPARISON_FILTER = re.compile(
    rf"(?P<number>{NUMERIC_LITERAL_SOURCE})\s*"
    r"(?:(?:원|%|퍼센트|개|건|명|대|곳|시간|분|초)\s*)?"
    r"(?P<comparison>인(?:\s+경우)?|짜리|이상|이하|초과|미만|"
    r"같(?:은|이)|보다\s*(?:큰|작은|많은|적은))"
)
_DATE_FILTER = re.compile(rf"(?P<number>{BASE_NUMBER_SOURCE})\s*(?:년|월|일)\b")
_RANK_FILTER = re.compile(
    rf"(?:상위|하위)\s*(?P<number>{NUMERIC_LITERAL_SOURCE})\s*" r"(?:개|건|명|대|곳)?"
)

_COMPARISON_OPERATORS: dict[str, ConstraintOperator] = {
    "인": "eq",
    "인 경우": "eq",
    "짜리": "eq",
    "같은": "eq",
    "같이": "eq",
    "이상": "gte",
    "이하": "lte",
    "초과": "gt",
    "미만": "lt",
    "보다 큰": "gt",
    "보다 많은": "gt",
    "보다 작은": "lt",
    "보다 적은": "lt",
}


def required_numeric_constraints(question: str) -> list[NumericConstraint]:
    """질문에서 DB 쿼리에 보존돼야 하는 값과 비교 의미를 추출한다."""
    constraints = [
        NumericConstraint(
            normalize_numeric_literal(match.group("number")),
            _COMPARISON_OPERATORS[" ".join(match.group("comparison").split())],
        )
        for match in _COMPARISON_FILTER.finditer(question)
    ]
    constraints.extend(
        NumericConstraint(normalize_numeric_literal(match.group("number")), "present")
        for match in _DATE_FILTER.finditer(question)
    )
    constraints.extend(
        NumericConstraint(normalize_numeric_literal(match.group("number")), "limit")
        for match in _RANK_FILTER.finditer(question)
    )
    return constraints


def required_numeric_filter_literals(question: str) -> set[str]:
    """호환용 공개 함수: 구조화된 숫자 조건의 정규화된 값 집합."""
    return {constraint.value for constraint in required_numeric_constraints(question)}


def _operator_matches(query: str, match: re.Match[str], operator: str) -> bool:
    before = query[max(0, match.start() - 12) : match.start()]
    after = query[match.end() : min(len(query), match.end() + 12)]
    before = re.sub(r"[\s'\"()]", "", before)
    after = re.sub(r"[\s'\"()]", "", after)

    if operator == "present":
        return True
    if operator == "limit":
        return bool(re.search(r"(?i)limit$", before))
    if operator == "eq":
        return bool(re.search(r"(?<![<>!])(?:=|:)$", before)) or bool(
            re.match(r"^(?<![<>!])=", after)
        )
    symbols = {
        "gte": (r">=$", r"^<="),
        "lte": (r"<=$", r"^>="),
        "gt": (r"(?<![<>=])>$", r"^<(?!=)"),
        "lt": (r"(?<![<>=])<$", r"^>(?!=)"),
    }
    left, right = symbols[operator]
    return bool(re.search(left, before) or re.match(right, after))


def missing_numeric_filter_literals(question: str, generated_query: str) -> set[str]:
    query_numbers = list(NUMERIC_LITERAL.finditer(generated_query))
    missing: set[str] = set()
    for constraint in required_numeric_constraints(question):
        candidates = [
            match
            for match in query_numbers
            if normalize_numeric_literal(match.group("number")) == constraint.value
        ]
        if not any(
            _operator_matches(generated_query, match, constraint.operator)
            for match in candidates
        ):
            missing.add(constraint.value)
    return missing
