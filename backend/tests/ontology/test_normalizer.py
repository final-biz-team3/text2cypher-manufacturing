from pathlib import Path

from ontology.loader import load_term_dictionary
from ontology.normalizer import normalize_query

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_DICTIONARY = load_term_dictionary(
    _PROJECT_ROOT / "ontology" / "manufacturing_terms.yaml"
)


def test_normalizes_business_terms_and_preserves_action_wording() -> None:
    result = normalize_query("협력사가 자재를 삭제해줘", _DICTIONARY)

    assert result["normalized_query"] == "공급업체가 부품를 삭제해줘"
    assert result["matched_terms"] == [
        {
            "original": "협력사",
            "canonical": "공급업체",
            "concept_id": "supplier",
            "concept_type": "BUSINESS",
            "target_type": "Supplier",
        },
        {
            "original": "자재",
            "canonical": "부품",
            "concept_id": "component",
            "concept_type": "BUSINESS",
            "target_type": "Product",
        },
    ]
    assert result["detected_actions"] == [
        {
            "original": "삭제",
            "canonical": "삭제",
            "action_type": "DELETE",
            "default_policy": "BLOCK",
        }
    ]


def test_unregistered_terms_pass_through_unchanged() -> None:
    query = "알 수 없는 내부 표현으로 현황을 부탁합니다"

    result = normalize_query(query, _DICTIONARY)

    assert result["normalized_query"] == query
    assert result["matched_terms"] == []


def test_longer_term_wins_over_shorter_term() -> None:
    result = normalize_query("생산 작업지시를 보여줘", _DICTIONARY)

    assert result["normalized_query"] == "작업지시를 보여줘"
    assert [item["original"] for item in result["matched_terms"]] == ["생산 작업지시"]


def test_english_terms_match_case_insensitively_as_whole_words() -> None:
    result = normalize_query("VENDOR의 COMPONENT를 show", _DICTIONARY)

    assert result["normalized_query"] == "공급업체의 부품를 show"
    assert len(result["matched_terms"]) == 2
    assert result["detected_actions"][0]["action_type"] == "READ"
