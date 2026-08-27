"""등록된 제조 동의어를 결정적으로 정규화한다."""

import re
from dataclasses import dataclass

from ontology.loader import build_term_index, normalize_lookup_key
from ontology.models import TermConcept, TermDictionary
from orchestrator.state import (
    AmbiguousTerm,
    DetectedAction,
    MatchedTerm,
    NormalizationResult,
)

_KOREAN_PARTICLES = (
    "들에게서",
    "들에게",
    "들에서",
    "들까지",
    "들부터",
    "들처럼",
    "들보다",
    "들의",
    "들은",
    "들이",
    "들을",
    "들과",
    "들로",
    "들도",
    "들만",
    "에게서",
    "으로",
    "에서",
    "에게",
    "까지",
    "부터",
    "처럼",
    "보다",
    "은",
    "는",
    "이",
    "가",
    "을",
    "를",
    "과",
    "와",
    "의",
    "에",
    "로",
    "도",
    "만",
    "별로",
    "별",
    "들",
)
_ENGLISH_READ_COMMANDS = {"show", "find", "list", "get", "search", "display"}


@dataclass(frozen=True)
class CompiledTerm:
    lookup_term: str
    candidates: tuple[TermConcept, ...]
    pattern: re.Pattern[str]


@dataclass(frozen=True)
class CompiledTermIndex:
    terms: tuple[CompiledTerm, ...]
    lookup_terms: frozenset[str]


def _term_pattern(term: str) -> re.Pattern[str]:
    escaped = re.escape(term)
    if term.isascii() and any(character.isalpha() for character in term):
        return re.compile(rf"(?<![A-Za-z0-9_]){escaped}(?![A-Za-z0-9_])", re.I)
    return re.compile(escaped, re.I)


def compile_term_dictionary(dictionary: TermDictionary) -> CompiledTermIndex:
    """정적 사전의 검색 순서와 정규식을 요청 처리 전에 한 번 계산한다."""
    term_index = build_term_index(dictionary)
    sorted_terms = sorted(term_index, key=lambda value: (-len(value), value))
    return CompiledTermIndex(
        terms=tuple(
            CompiledTerm(
                lookup_term=term,
                candidates=tuple(term_index[term]),
                pattern=_term_pattern(term),
            )
            for term in sorted_terms
        ),
        lookup_terms=frozenset(term_index),
    )


def _has_korean_business_boundary(
    query: str, start: int, end: int, *, allowed_prefixes: list[str]
) -> bool:
    before = query[:start]
    after = query[end:]
    if before and before[-1].isalnum():
        has_allowed_prefix = any(
            before.endswith(prefix)
            and (len(before) == len(prefix) or not before[-len(prefix) - 1].isalnum())
            for prefix in allowed_prefixes
        )
        if not has_allowed_prefix:
            return False
    if after and after[0].isalnum():
        return any(
            after.startswith(particle)
            and (len(after) == len(particle) or not after[len(particle)].isalnum())
            for particle in _KOREAN_PARTICLES
        )
    return True


def _concept_matches_boundary(
    query: str,
    start: int,
    end: int,
    term: str,
    concept: TermConcept,
    lookup_terms: set[str],
) -> bool:
    if concept.concept_type == "BUSINESS" and term.isascii():
        previous = re.search(r"([A-Z][A-Za-z0-9&.-]*)\s+$", query[:start])
        previous_is_entity_name = bool(
            previous
            and previous.group(1).casefold()
            not in _ENGLISH_READ_COMMANDS | lookup_terms
        )
        next_is_title_case = bool(re.match(r"\s+[A-Z][A-Za-z0-9&.-]*", query[end:]))
        if previous_is_entity_name or next_is_title_case:
            return False
    return not (
        concept.concept_type == "BUSINESS"
        and any("가" <= character <= "힣" for character in term)
        and not _has_korean_business_boundary(
            query,
            start,
            end,
            allowed_prefixes=concept.allowed_prefixes,
        )
    )


def _has_final_consonant(word: str) -> bool:
    """마지막 한글 음절에 받침이 있는지 반환한다."""
    if not word:
        return False
    codepoint = ord(word[-1])
    return 0xAC00 <= codepoint <= 0xD7A3 and (codepoint - 0xAC00) % 28 != 0


def _adjust_particle(query: str, end: int, canonical: str) -> tuple[int, str]:
    """치환 직후의 한글 조사를 표준어 받침에 맞춘다."""
    pairs = {"은": "는", "이": "가", "을": "를", "과": "와"}
    if end >= len(query):
        return end, ""
    particle = query[end]
    all_particles = set(pairs) | set(pairs.values())
    if particle not in all_particles:
        return end, ""
    if _has_final_consonant(canonical):
        adjusted = next(
            (
                consonant
                for consonant, vowel in pairs.items()
                if particle in {consonant, vowel}
            ),
            particle,
        )
    else:
        adjusted = next(
            (
                vowel
                for consonant, vowel in pairs.items()
                if particle in {consonant, vowel}
            ),
            particle,
        )
    return end + 1, adjusted


def normalize_query(
    query: str,
    dictionary: TermDictionary,
    *,
    compiled_index: CompiledTermIndex | None = None,
) -> NormalizationResult:
    """업무 용어를 치환하고 행동 용어는 별도 목록으로 반환한다."""
    normalized = query
    matched_terms: list[MatchedTerm] = []
    detected_actions: list[DetectedAction] = []
    ambiguous_terms: list[AmbiguousTerm] = []
    compiled = compiled_index or compile_term_dictionary(dictionary)
    lookup_terms = set(compiled.lookup_terms)
    occupied: list[tuple[int, int]] = []
    replacements: list[tuple[int, int, str]] = []

    for compiled_term in compiled.terms:
        lookup_term = compiled_term.lookup_term
        for match in compiled_term.pattern.finditer(query):
            span = match.span()
            candidates = [
                concept
                for concept in compiled_term.candidates
                if _concept_matches_boundary(
                    query,
                    *span,
                    lookup_term,
                    concept,
                    lookup_terms,
                )
            ]
            if not candidates:
                continue
            if any(span[0] < end and start < span[1] for start, end in occupied):
                continue
            occupied.append(span)
            original = match.group(0)
            if len(candidates) > 1:
                ambiguous_terms.append(
                    {
                        "original": original,
                        "candidates": [
                            {
                                "concept_id": concept.concept_id,
                                "canonical": concept.canonical,
                                "concept_type": concept.concept_type,
                                "target_type": concept.target_type,
                            }
                            for concept in candidates
                        ],
                    }
                )
                continue

            concept = candidates[0]
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
            replacement_end, particle = _adjust_particle(
                query, span[1], concept.canonical
            )
            replacements.append(
                (span[0], replacement_end, concept.canonical + particle)
            )

    # 원문의 위치를 기준으로 뒤에서부터 바꿔 offset 변화를 피한다.
    for start, end, canonical in sorted(replacements, reverse=True):
        normalized = normalized[:start] + canonical + normalized[end:]

    return {
        "normalized_query": normalized,
        "matched_terms": matched_terms,
        "detected_actions": detected_actions,
        "normalization_status": (
            "NEEDS_CLARIFICATION" if ambiguous_terms else "NORMALIZED"
        ),
        "ambiguous_terms": ambiguous_terms,
    }
