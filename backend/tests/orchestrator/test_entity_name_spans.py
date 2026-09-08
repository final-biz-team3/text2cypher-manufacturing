"""이름 표기 정규화의 원문 좌표, 경계, 긴 입력 처리 비용을 검증한다."""

import unicodedata

import pytest

import orchestrator.nodes.resolve_entity as resolver


@pytest.mark.parametrize(
    "literal,name,remove_commas",
    [
        ("Touring-1000  Yellow, 54", "touring-1000 yellow,54", False),
        ("Touring-1000 Yellow,54", "Touring-1000 Yellow, 54", False),
        ("Touring-1000 Yellow , \t54", "Touring-1000 Yellow,54", False),
        ("Touring-1000 Yellow, 54", "Touring-1000 Yellow 54", True),
        ("Touring-1000 Yellow,54", "Touring-1000 Yellow 54", True),
        ("Touring-1000\u00a0Yellow,\n54", "Touring-1000 Yellow 54", True),
        ("Cafe\u0301, 54", "CAFÉ,54", False),
        ("Straße, 54", "STRASSE 54", True),
    ],
)
def test_normalized_spans_use_nfc_question_coordinates(literal, name, remove_commas):
    prefix = "ß 앞쪽 "  # casefold가 길이를 늘려도 뒤 이름의 좌표는 이동하지 않는다.
    query = prefix + literal + "의 재고"
    spans = resolver._normalized_name_spans(query, name, remove_commas=remove_commas)
    nfc_literal = unicodedata.normalize("NFC", literal)
    assert spans == [(len(prefix), len(prefix) + len(nfc_literal))]
    assert resolver._literal_name_spans(query, literal) == spans


@pytest.mark.parametrize("remove_commas", [False, True])
def test_normalized_spans_keep_independent_occurrences_and_ascii_boundaries(
    remove_commas,
):
    query = "XTouring  Yellow,54 Touring\tYellow, 54의 Touring Yellow,54Z"
    literal = "Touring\tYellow, 54"
    start = query.index(literal)
    name = "touring yellow 54" if remove_commas else "touring yellow,54"
    assert resolver._normalized_name_spans(
        query, name, remove_commas=remove_commas
    ) == [(start, start + len(literal))]
    query = "Touring  Yellow,54 와 Touring Yellow,54"
    spans = resolver._normalized_name_spans(query, name, remove_commas=remove_commas)
    assert len(spans) == 2
    assert spans[0][1] < spans[1][0]


@pytest.mark.parametrize("query,name", [("ß", "s"), ("abc", "b"), ("", ""), ("a", " ")])
def test_normalized_spans_reject_partial_characters_and_empty_names(query, name):
    assert resolver._normalized_name_spans(query, name, remove_commas=False) == []


def test_commas_are_only_optional_in_explicit_duplicate_comparison():
    query = "Touring Yellow,54"
    assert (
        resolver._normalized_name_spans(query, "Touring Yellow 54", remove_commas=False)
        == []
    )
    assert resolver._normalized_name_spans(
        query, "Touring Yellow 54", remove_commas=True
    ) == [(0, len(query))]
    assert (
        resolver._normalized_name_spans(query, "Touring Yelow 54", remove_commas=True)
        == []
    )


@pytest.mark.parametrize("remove_commas", [False, True])
def test_long_input_does_not_renormalize_substrings(monkeypatch, remove_commas):
    """시간 임계값 대신 정규화 호출 수로 O(n³) 구현의 재도입을 즉시 차단한다."""
    compare = resolver._normalized_name_comparison
    calls = []

    def counted(value, *, remove_commas):
        calls.append(value)
        assert len(calls) == 1, "부분 문자열을 반복 정규화하면 안 된다"
        return compare(value, remove_commas=remove_commas)

    monkeypatch.setattr(resolver, "_normalized_name_comparison", counted)
    query = "가" * 100_000 + " Touring-1000 Yellow, 54"
    assert (
        resolver._normalized_name_spans(
            query, "Touring-1000 Yelow 54", remove_commas=remove_commas
        )
        == []
    )
    assert calls == ["Touring-1000 Yelow 54"]
