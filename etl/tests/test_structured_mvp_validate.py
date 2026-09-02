"""쓰기 전 순수 검사 + 세션 주입형 건수/존재 확인(가짜 세션으로 DB 없이 검증)."""

from typing import Any

from structured_mvp_validate import (
    _entity_exists,
    count_nodes_by_label,
    count_relationships_by_type,
    find_dangling_relationship_rows,
    find_duplicate_key_rows,
    find_rows_with_null_key,
    quantities_match,
)


class _FakeResult:
    def __init__(self, record: Any) -> None:
        self._record = record

    def single(self) -> Any:
        return self._record


class _FakeSession:
    """호출된 쿼리를 기록하고 준비된 record를 순서대로 돌려주는 가짜 세션."""

    def __init__(self, records: list[Any]) -> None:
        self._records = list(records)
        self.runs: list[tuple[str, dict[str, Any]]] = []

    def run(self, query: str, **params: Any) -> _FakeResult:
        self.runs.append((query, params))
        return _FakeResult(self._records.pop(0))


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


def test_count_nodes_by_label_maps_each_label_to_its_count() -> None:
    session = _FakeSession([{"c": 504}, {"c": 104}])

    counts = count_nodes_by_label(session, ["Product", "Supplier"])

    assert counts == {"Product": 504, "Supplier": 104}
    assert "MATCH (n:Product)" in session.runs[0][0]


def test_count_relationships_by_type_maps_each_type_to_its_count() -> None:
    session = _FakeSession([{"c": 42}])

    counts = count_relationships_by_type(session, ["SUPPLIES"])

    assert counts == {"SUPPLIES": 42}


def test_entity_exists_is_true_when_count_positive() -> None:
    session = _FakeSession([{"c": 1}])

    assert _entity_exists(session, "Product", "productId", 680, None) is True


def test_entity_exists_is_false_when_count_zero() -> None:
    session = _FakeSession([{"c": 0}])

    assert _entity_exists(session, "Product", "productId", 999, None) is False
