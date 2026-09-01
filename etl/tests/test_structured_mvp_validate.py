"""관계 적재 전 참조 무결성 검사(순수 함수, DB 접속 없이 메모리에서 계산)."""

from structured_mvp_validate import (
    find_dangling_relationship_rows,
    find_duplicate_key_rows,
    find_rows_with_null_key,
    quantities_match,
)


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


def test_quantities_match_true_for_exact_value() -> None:
    assert quantities_match(80.0, 80) is True


def test_quantities_match_true_within_floating_point_epsilon() -> None:
    assert quantities_match(79.99999999999997, 80) is True


def test_quantities_match_false_for_real_mismatch() -> None:
    assert quantities_match(79.9, 80) is False


def test_find_duplicate_key_rows_finds_repeated_single_column_key() -> None:
    rows = [
        {"bomId": 1, "quantityPerAssembly": 2},
        {"bomId": 2, "quantityPerAssembly": 3},
        {"bomId": 1, "quantityPerAssembly": 5},
    ]

    duplicates = find_duplicate_key_rows(rows, ("bomId",))

    assert duplicates == [(1,)]


def test_find_duplicate_key_rows_finds_repeated_composite_key() -> None:
    rows = [
        {"workOrderId": 1, "productId": 10},
        {"workOrderId": 1, "productId": 10},
        {"workOrderId": 2, "productId": 20},
    ]

    duplicates = find_duplicate_key_rows(rows, ("workOrderId", "productId"))

    assert duplicates == [(1, 10)]


def test_find_duplicate_key_rows_returns_empty_when_all_unique() -> None:
    rows = [{"bomId": 1}, {"bomId": 2}, {"bomId": 3}]

    assert find_duplicate_key_rows(rows, ("bomId",)) == []


def test_find_rows_with_null_key_finds_row_missing_single_column() -> None:
    rows = [{"bomId": 1}, {"bomId": None}, {"bomId": 3}]

    missing = find_rows_with_null_key(rows, ("bomId",))

    assert missing == [{"bomId": None}]


def test_find_rows_with_null_key_checks_all_columns_in_composite_key() -> None:
    rows = [
        {"workOrderId": 1, "productId": 10},
        {"workOrderId": None, "productId": 20},
        {"workOrderId": 3, "productId": None},
    ]

    missing = find_rows_with_null_key(rows, ("workOrderId", "productId"))

    assert missing == [
        {"workOrderId": None, "productId": 20},
        {"workOrderId": 3, "productId": None},
    ]


def test_find_rows_with_null_key_returns_empty_when_all_present() -> None:
    rows = [{"bomId": 1}, {"bomId": 2}]

    assert find_rows_with_null_key(rows, ("bomId",)) == []
