"""psycopg2가 반환한 Python date/datetime 객체를 loading_rules.md의 Cypher가
기대하는 ISO 문자열로 바꾸는 순수 함수를 검증한다. 실제 DB 연결이 필요한
extract_rows()는 로컬 docker 환경에서 통합 검증한다."""

import datetime
import decimal

from structured_mvp_extract import coerce_decimals, normalize_row


def test_normalize_row_converts_datetime_to_iso_string() -> None:
    row = {
        "workOrderId": 17747,
        "sourceModifiedAt": datetime.datetime(2014, 6, 1, 10, 30, 5),
    }

    result = normalize_row(row, datetime_columns=("sourceModifiedAt",))

    assert result["sourceModifiedAt"] == "2014-06-01T10:30:05"
    assert result["workOrderId"] == 17747


def test_normalize_row_converts_date_to_iso_string() -> None:
    row = {"bomId": 1, "startDate": datetime.date(2011, 4, 25), "endDate": None}

    result = normalize_row(row, date_columns=("startDate", "endDate"))

    assert result["startDate"] == "2011-04-25"
    assert result["endDate"] is None


def test_normalize_row_handles_date_and_datetime_columns_in_one_call() -> None:
    row = {
        "startDate": datetime.date(2011, 4, 25),
        "sourceModifiedAt": datetime.datetime(2014, 6, 1, 10, 30, 5),
        "other": "x",
    }

    result = normalize_row(
        row,
        date_columns=("startDate",),
        datetime_columns=("sourceModifiedAt",),
    )

    assert result["startDate"] == "2011-04-25"
    assert result["sourceModifiedAt"] == "2014-06-01T10:30:05"
    assert result["other"] == "x"


def test_normalize_row_leaves_null_datetime_as_none() -> None:
    row = {"scrapReasonId": None}

    result = normalize_row(row, datetime_columns=("scrapReasonId",))

    assert result["scrapReasonId"] is None


def test_coerce_decimals_converts_decimal_to_float() -> None:
    row = {"bomId": 1, "quantityPerAssembly": decimal.Decimal("1.00")}

    result = coerce_decimals(row)

    assert result["quantityPerAssembly"] == 1.0
    assert isinstance(result["quantityPerAssembly"], float)
    assert result["bomId"] == 1


def test_coerce_decimals_leaves_non_decimal_values_untouched() -> None:
    row = {"name": "Paint - Black", "active": True, "startDate": None}

    result = coerce_decimals(row)

    assert result == row
