"""쓰기 전 검증(validate_before_write)은 순수 함수라 DB 없이 추출 결과
fixture(dict)만으로 검사한다. extract_all/load_all/validate_after_load는 DB
세션이 필요해 로컬 docker 통합 검증에서 확인한다."""

from run_structured_mvp_sync import validate_before_write


def _valid_nodes() -> dict[str, list[dict]]:
    return {
        "Product": [{"productId": 1}, {"productId": 2}],
        "Supplier": [{"supplierId": 10}],
        "WorkOrder": [{"workOrderId": 100}],
        "RoutingOperation": [{"routingOperationKey": "100-1-1"}],
        "Location": [{"locationId": 50}],
        "ScrapReason": [{"scrapReasonId": 7}],
    }


def _valid_rels() -> dict[str, list[dict]]:
    return {
        "SUPPLIES": [{"supplyKey": "10-1", "supplierId": 10, "productId": 1}],
        "REQUIRES_COMPONENT": [
            {"bomId": 1, "assemblyProductId": 1, "componentProductId": 2}
        ],
        "PRODUCES": [{"workOrderId": 100, "productId": 1}],
        "HAS_OPERATION": [{"workOrderId": 100, "routingOperationKey": "100-1-1"}],
        "PERFORMED_AT": [{"routingOperationKey": "100-1-1", "locationId": 50}],
        "SCRAPPED_DUE_TO": [{"workOrderId": 100, "scrapReasonId": 7}],
    }


def test_valid_input_returns_empty_list() -> None:
    assert validate_before_write(_valid_nodes(), _valid_rels()) == []


def test_reports_label_with_zero_rows() -> None:
    nodes = _valid_nodes()
    nodes["Location"] = []

    failures = validate_before_write(nodes, _valid_rels())

    assert any("Location 추출 결과가 0건입니다" in f for f in failures)


def test_reports_relationship_type_with_zero_rows() -> None:
    rels = _valid_rels()
    rels["PRODUCES"] = []

    failures = validate_before_write(_valid_nodes(), rels)

    assert any("PRODUCES 추출 결과가 0건입니다" in f for f in failures)


def test_reports_null_unique_key_row() -> None:
    nodes = _valid_nodes()
    nodes["Product"].append({"productId": None})

    failures = validate_before_write(nodes, _valid_rels())

    assert any("Product: productId이 NULL인 행" in f for f in failures)


def test_reports_duplicate_unique_key_row() -> None:
    nodes = _valid_nodes()
    nodes["Supplier"] = [{"supplierId": 10}, {"supplierId": 10}]

    failures = validate_before_write(nodes, _valid_rels())

    assert any("Supplier: 중복된 supplierId" in f for f in failures)


def test_reports_dangling_relationship_row() -> None:
    rels = _valid_rels()
    rels["SUPPLIES"] = [{"supplyKey": "x", "supplierId": 999, "productId": 1}]

    failures = validate_before_write(_valid_nodes(), rels)

    assert any("SUPPLIES: 참조 누락" in f for f in failures)
