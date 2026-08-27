import os

import psycopg
from psycopg import Connection

_connection: Connection | None = None
_history_write_connection: Connection | None = None


def _connect(*, read_only: bool) -> Connection:
    return psycopg.connect(
        host=os.getenv("POSTGRES_HOST", "localhost"),
        port=os.getenv("POSTGRES_PORT", "5432"),
        dbname=os.getenv("POSTGRES_DB", "postgres"),
        user=os.getenv("POSTGRES_APP_USER", "text2cypher_reader"),
        password=os.getenv("POSTGRES_APP_PASSWORD", "changeme_local"),
        options="-c default_transaction_read_only=on" if read_only else "",
    )


def get_connection() -> Connection:
    global _connection
    if _connection is None or _connection.closed:
        _connection = _connect(read_only=True)
    return _connection


def get_history_write_connection() -> Connection:
    """대화기록 INSERT에만 사용하는 제한된 쓰기 세션을 반환한다.

    DB 역할은 쿼리 조회 세션과 같지만 프로비저닝에서
    ``app.conversation_history`` INSERT와 해당 시퀀스 사용만 추가로 허용한다.
    사용자 생성 SQL에는 이 커넥션을 절대 전달하지 않는다.
    """
    global _history_write_connection
    if _history_write_connection is None or _history_write_connection.closed:
        _history_write_connection = _connect(read_only=False)
    return _history_write_connection


def close_connection() -> None:
    global _connection, _history_write_connection
    if _connection is not None:
        _connection.close()
        _connection = None
    if _history_write_connection is not None:
        _history_write_connection.close()
        _history_write_connection = None
