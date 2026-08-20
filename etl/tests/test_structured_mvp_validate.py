"""관계 적재 전 참조 무결성 검사(순수 함수, DB 접속 없이 메모리에서 계산)."""

from structured_mvp_validate import counts_are_equal, find_dangling_relationship_rows


def test_finds_relationship_row_whose_from_key_is_missing() -> None:
    rows = [
        {"assemblyProductId": 680, "componentProductId": 492},
        {"assemblyProductId": 999, "componentProductId": 492},  # 999는 Product에 없음
    ]

    dangling = find_dangling_relationship_rows(
        rows,
        from_key="assemblyProductId",
        to_key="componentProductId",
        from_ids={680, 492},
        to_ids={680, 492},
    )

    assert dangling == [{"assemblyProductId": 999, "componentProductId": 492}]


def test_returns_empty_list_when_all_references_exist() -> None:
    rows = [{"assemblyProductId": 680, "componentProductId": 492}]

    dangling = find_dangling_relationship_rows(
        rows,
        from_key="assemblyProductId",
        to_key="componentProductId",
        from_ids={680, 492},
        to_ids={680, 492},
    )

    assert dangling == []


def test_finds_relationship_row_whose_to_key_is_missing() -> None:
    rows = [{"workOrderId": 17747, "scrapReasonId": 999}]

    dangling = find_dangling_relationship_rows(
        rows,
        from_key="workOrderId",
        to_key="scrapReasonId",
        from_ids={17747},
        to_ids={8},
    )

    assert dangling == [{"workOrderId": 17747, "scrapReasonId": 999}]


def test_counts_are_equal_true_for_identical_snapshots() -> None:
    first = {"Product": 504, "Supplier": 104}
    second = {"Product": 504, "Supplier": 104}

    assert counts_are_equal(first, second) is True


def test_counts_are_equal_false_when_any_count_differs() -> None:
    first = {"Product": 504, "Supplier": 104}
    second = {"Product": 504, "Supplier": 103}

    assert counts_are_equal(first, second) is False
