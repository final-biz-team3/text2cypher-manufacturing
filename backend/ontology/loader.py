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
    build_term_map(dictionary)
    return dictionary


def build_term_map(dictionary: TermDictionary) -> dict[str, TermConcept]:
    term_map: dict[str, TermConcept] = {}
    for concept in dictionary.concepts:
        for term in concept.terms:
            key = normalize_lookup_key(term)
            if not key:
                raise ValueError(f"Empty term in concept {concept.concept_id}.")
            previous = term_map.get(key)
            if previous is not None and previous.concept_id != concept.concept_id:
                raise ValueError(
                    f"Duplicate term {term!r}: {previous.concept_id}, "
                    f"{concept.concept_id}."
                )
            term_map[key] = concept
    return term_map
