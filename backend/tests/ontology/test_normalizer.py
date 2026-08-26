from pathlib import Path

import pytest

from ontology.loader import load_term_dictionary
from ontology.normalizer import normalize_query

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_DICTIONARY = load_term_dictionary(
    _PROJECT_ROOT / "ontology" / "manufacturing_terms.yaml"
)


def test_normalizes_business_terms_and_preserves_action_wording() -> None:
    result = normalize_query("협력사가 자재를 삭제해줘", _DICTIONARY)

    assert result["normalized_query"] == "공급업체가 부품을 삭제해줘"
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


@pytest.mark.parametrize(
    "query",
    [
        "소모품 목록",
        "공급사슬 현황",
        "한빛협력사 현황",
        "한빛우수협력사 현황",
        "협력사이름을 알려줘",
        "공급사의뢰 현황",
        "ABC협력사 현황",
        "123협력사 현황",
    ],
)
def test_does_not_replace_korean_term_inside_another_word(query: str) -> None:
    result = normalize_query(query, _DICTIONARY)

    assert result["normalized_query"] == query
    assert result["matched_terms"] == []


@pytest.mark.parametrize(
    "query, expected",
    [
        ("협력사들의 목록", "공급업체들의 목록"),
        ("협력사별 현황", "공급업체별 현황"),
        ("협력사에게서 받은 자재", "공급업체에게서 받은 부품"),
        ("협력사가 공급하는 자재", "공급업체가 공급하는 부품"),
        ("우수협력사 목록", "우수공급업체 목록"),
        ("핵심협력사 목록", "핵심공급업체 목록"),
        ("전략협력사 목록", "전략공급업체 목록"),
        ("1차협력사 목록", "1차공급업체 목록"),
    ],
)
def test_normalizes_common_korean_prefixes_and_suffixes(
    query: str, expected: str
) -> None:
    result = normalize_query(query, _DICTIONARY)

    assert result["normalized_query"] == expected


def test_longer_term_wins_over_shorter_term() -> None:
    result = normalize_query("생산 작업지시를 보여줘", _DICTIONARY)

    assert result["normalized_query"] == "작업지시를 보여줘"
    assert [item["original"] for item in result["matched_terms"]] == ["생산 작업지시"]


def test_english_terms_match_case_insensitively_as_whole_words() -> None:
    result = normalize_query("VENDOR의 COMPONENT를 show", _DICTIONARY)

    assert result["normalized_query"] == "공급업체의 부품을 show"
    assert len(result["matched_terms"]) == 2
    assert result["detected_actions"][0]["action_type"] == "READ"


def test_english_term_inside_title_case_entity_name_is_preserved() -> None:
    query = "Vendor Components Ltd 현황을 보여줘"

    result = normalize_query(query, _DICTIONARY)

    assert result["normalized_query"] == query


@pytest.mark.parametrize(
    "query, expected",
    [
        ("Show Vendor list", "Show 공급업체 list"),
        ("Find Component inventory", "Find 부품 재고"),
    ],
)
def test_title_case_term_after_read_command_is_normalized(
    query: str, expected: str
) -> None:
    assert normalize_query(query, _DICTIONARY)["normalized_query"] == expected


@pytest.mark.parametrize(
    "query, expected",
    [
        ("자품 목록을 보여줘", "부품 목록을 보여줘"),
        ("하위품 목록을 보여줘", "부품 목록을 보여줘"),
        ("모품 목록을 보여줘", "완제품 목록을 보여줘"),
        ("상위품 목록을 보여줘", "완제품 목록을 보여줘"),
        ("작업오더 17747을 보여줘", "작업지시 17747을 보여줘"),
        ("WO 17747을 보여줘", "작업지시 17747을 보여줘"),
        ("스크랩 사유를 알려줘", "폐기사유를 알려줘"),
        ("현재고를 알려줘", "재고를 알려줘"),
        ("실재고를 알려줘", "재고를 알려줘"),
    ],
)
def test_normalizes_project_manufacturing_terms(query: str, expected: str) -> None:
    result = normalize_query(query, _DICTIONARY)

    assert result["normalized_query"] == expected
    assert len(result["matched_terms"]) == 1


def test_ambiguous_term_requires_clarification() -> None:
    dictionary = _DICTIONARY.model_copy(deep=True)
    dictionary.concepts.append(
        dictionary.concepts[0].model_copy(
            update={
                "concept_id": "partner",
                "canonical": "파트너",
                "target_type": "Partner",
                "terms": ["협력사"],
                "allowed_prefixes": [],
            }
        )
    )

    result = normalize_query("협력사 목록을 보여줘", dictionary)

    assert result["normalization_status"] == "NEEDS_CLARIFICATION"
    assert result["normalized_query"] == "협력사 목록을 보여줘"
    assert [
        candidate["concept_id"]
        for candidate in result["ambiguous_terms"][0]["candidates"]
    ] == ["supplier", "partner"]
