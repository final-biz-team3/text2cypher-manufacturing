"""PostgreSQL에서 구조화 MVP 노드·관계 원본 행을 읽어 Neo4j MERGE에 바로
쓸 수 있는 형태(날짜/시간을 ISO 문자열로)로 정규화한다."""

from typing import Any

import psycopg2
import psycopg2.extras


def normalize_row(
    row: dict[str, Any],
    *,
    date_columns: tuple[str, ...] = (),
    datetime_columns: tuple[str, ...] = (),
) -> dict[str, Any]:
    """date/datetime 컬럼을 ISO-8601 문자열로 바꾼다. NULL은 그대로 둔다.

    structured_mvp_loading_rules.md의 MERGE 예시가 date(row.x)/localdatetime(row.x)로
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
        return [dict(row) for row in cursor.fetchall()]
