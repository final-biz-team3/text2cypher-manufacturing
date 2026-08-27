from pathlib import Path

import pytest

from ontology.loader import (
    OntologyLoadError,
    build_term_index,
    build_term_map,
    load_term_dictionary,
)
from ontology.models import TermDictionary

_PROJECT_ROOT = Path(__file__).resolve().parents[3]


def test_project_dictionary_loads_and_contains_korean_and_english_terms() -> None:
    dictionary = load_term_dictionary(
        _PROJECT_ROOT / "ontology" / "manufacturing_terms.yaml"
    )
    term_map = build_term_map(dictionary)

    assert term_map["협력사"].canonical == "공급업체"
    assert term_map["vendor"].canonical == "공급업체"
    assert term_map["delete"].action_type == "DELETE"


def test_duplicate_term_across_concepts_is_rejected() -> None:
    dictionary = TermDictionary.model_validate(
        {
            "version": "1",
            "concepts": [
                {
                    "conceptId": "one",
                    "conceptType": "BUSINESS",
                    "canonical": "첫째",
                    "terms": ["중복"],
                },
                {
                    "conceptId": "two",
                    "conceptType": "BUSINESS",
                    "canonical": "둘째",
                    "terms": ["중복"],
                },
            ],
        }
    )

    assert [concept.concept_id for concept in build_term_index(dictionary)["중복"]] == [
        "one",
        "two",
    ]

    with pytest.raises(ValueError, match="Ambiguous term"):
        build_term_map(dictionary)


def test_missing_ontology_fails_closed_with_clear_error(tmp_path: Path) -> None:
    missing = tmp_path / "missing.yaml"

    with pytest.raises(OntologyLoadError, match="서버 시작을 중단"):
        load_term_dictionary(missing)
