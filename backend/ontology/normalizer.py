"""등록된 제조 동의어를 결정적으로 정규화한다."""

import re

from ontology.loader import build_term_map, normalize_lookup_key
from ontology.models import TermDictionary
from orchestrator.state import DetectedAction, MatchedTerm, NormalizationResult


def _term_pattern(term: str) -> re.Pattern[str]:
    escaped = re.escape(term)
    if term.isascii() and any(character.isalpha() for character in term):
        return re.compile(rf"(?<![A-Za-z0-9_]){escaped}(?![A-Za-z0-9_])", re.I)
    return re.compile(escaped, re.I)


def normalize_query(query: str, dictionary: TermDictionary) -> NormalizationResult:
    """업무 용어를 치환하고 행동 용어는 별도 목록으로 반환한다."""
    normalized = query
    matched_terms: list[MatchedTerm] = []
    detected_actions: list[DetectedAction] = []
    term_map = build_term_map(dictionary)

    # 복합 표현이 짧은 표현보다 먼저 매칭되도록 한다.
    terms = sorted(term_map, key=lambda value: (-len(value), value))
    occupied: list[tuple[int, int]] = []
    replacements: list[tuple[int, int, str]] = []

    for lookup_term in terms:
        concept = term_map[lookup_term]
        for match in _term_pattern(lookup_term).finditer(query):
            span = match.span()
            if any(span[0] < end and start < span[1] for start, end in occupied):
                continue
            occupied.append(span)
            original = match.group(0)
            if concept.concept_type == "ACTION":
                assert concept.action_type is not None
                assert concept.default_policy is not None
                detected_actions.append(
                    {
                        "original": original,
                        "canonical": concept.canonical,
                        "action_type": concept.action_type,
                        "default_policy": concept.default_policy,
                    }
                )
                continue

            if normalize_lookup_key(original) == normalize_lookup_key(
                concept.canonical
            ):
                continue
            matched_terms.append(
                {
                    "original": original,
                    "canonical": concept.canonical,
                    "concept_id": concept.concept_id,
                    "concept_type": "BUSINESS",
                    "target_type": concept.target_type,
                }
            )
            replacements.append((span[0], span[1], concept.canonical))

    # 원문의 위치를 기준으로 뒤에서부터 바꿔 offset 변화를 피한다.
    for start, end, canonical in sorted(replacements, reverse=True):
        normalized = normalized[:start] + canonical + normalized[end:]

    return {
        "normalized_query": normalized,
        "matched_terms": matched_terms,
        "detected_actions": detected_actions,
    }
