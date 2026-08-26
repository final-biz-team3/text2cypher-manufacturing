"""제조 용어 YAML을 읽고 중복 없는 검색 맵을 만든다."""

from pathlib import Path

import yaml

from ontology.models import TermConcept, TermDictionary


def normalize_lookup_key(term: str) -> str:
    """영문 대소문자와 연속 공백을 검색에 영향 없도록 정규화한다."""
    return " ".join(term.casefold().split())


def load_term_dictionary(path: Path) -> TermDictionary:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    dictionary = TermDictionary.model_validate(data)
    build_term_index(dictionary)
    return dictionary


def build_term_index(dictionary: TermDictionary) -> dict[str, list[TermConcept]]:
    term_index: dict[str, list[TermConcept]] = {}
    for concept in dictionary.concepts:
        for term in concept.terms:
            key = normalize_lookup_key(term)
            if not key:
                raise ValueError(f"Empty term in concept {concept.concept_id}.")
            candidates = term_index.setdefault(key, [])
            if all(item.concept_id != concept.concept_id for item in candidates):
                candidates.append(concept)
    return term_index


def build_term_map(dictionary: TermDictionary) -> dict[str, TermConcept]:
    """호환용 단일 맵. 모호한 term은 자동 선택하지 않는다."""
    term_map: dict[str, TermConcept] = {}
    for term, candidates in build_term_index(dictionary).items():
        if len(candidates) != 1:
            concept_ids = ", ".join(item.concept_id for item in candidates)
            raise ValueError(f"Ambiguous term {term!r}: {concept_ids}.")
        term_map[term] = candidates[0]
    return term_map
