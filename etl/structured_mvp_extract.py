"""PostgreSQL에서 구조화 MVP 노드·관계 원본 행을 읽어 Neo4j MERGE에 바로
쓸 수 있는 형태(날짜/시간을 ISO 문자열로)로 정규화한다."""

import decimal
from typing import Any

import psycopg2
import psycopg2.extras


def coerce_decimals(row: dict[str, Any]) -> dict[str, Any]:
    """psycopg2가 PostgreSQL numeric(quantityPerAssembly 등)을 돌려줄 때 쓰는
    decimal.Decimal을 float으로 바꾼다.

    Neo4j 드라이버(Bolt 패킹)는 Decimal을 지원하지 않아 그대로 넘기면
    "Values of type <class 'decimal.Decimal'> are not supported"로 실패한다
    (실행 검증, 2026-08-20, REQUIRES_COMPONENT.quantityPerAssembly에서 발견).
    """
    return {
        key: float(value) if isinstance(value, decimal.Decimal) else value
        for key, value in row.items()
    }


def normalize_row(
    row: dict[str, Any],
    *,
    date_columns: tuple[str, ...] = (),
    datetime_columns: tuple[str, ...] = (),
) -> dict[str, Any]:
    """date/datetime 컬럼을 ISO-8601 문자열로 바꾼다. NULL은 그대로 둔다.

    docs/design/3-structured_mvp_loading_rules.md의 MERGE 예시가 date(row.x)/localdatetime(row.x)로
    문자열을 기대하므로, psycopg2가 돌려주는 datetime.date/datetime 객체를 여기서
    미리 문자열로 바꿔 그 Cypher를 그대로 재사용할 수 있게 한다.
    """
    normalized = dict(row)
    for column in date_columns:
        value = normalized.get(column)
        if value is not None:
            normalized[column] = value.isoformat()
    for column in datetime_columns:
        value = normalized.get(column)
        if value is not None:
            normalized[column] = value.isoformat()
    return normalized


def extract_rows(
    conn: psycopg2.extensions.connection, sql: str
) -> list[dict[str, Any]]:
    """SQL을 실행해 컬럼명을 키로 하는 dict 행 목록을 반환한다."""
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
        cursor.execute(sql)
        return [coerce_decimals(dict(row)) for row in cursor.fetchall()]
