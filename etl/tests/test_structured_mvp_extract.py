"""psycopg2가 반환한 Python date/datetime 객체를 loading_rules.md의 Cypher가
기대하는 ISO 문자열로 바꾸는 순수 함수를 검증한다. 실제 DB 연결이 필요한
extract_rows()는 로컬 docker 환경에서 통합 검증한다."""

import datetime

from structured_mvp_extract import normalize_row


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


def test_normalize_row_leaves_null_datetime_as_none() -> None:
    row = {"scrapReasonId": None}

    result = normalize_row(row, datetime_columns=("scrapReasonId",))

    assert result["scrapReasonId"] is None
