import os

import psycopg
from psycopg import Connection

_connection: Connection | None = None


def get_connection() -> Connection:
    global _connection
    if _connection is None or _connection.closed:
        _connection = psycopg.connect(
            host=os.getenv("POSTGRES_HOST", "localhost"),
            port=os.getenv("POSTGRES_PORT", "5432"),
            dbname=os.getenv("POSTGRES_DB", "postgres"),
            user=os.getenv("POSTGRES_APP_USER", "text2cypher_reader"),
            password=os.getenv("POSTGRES_APP_PASSWORD", "changeme_local"),
            options="-c default_transaction_read_only=on",
        )
    return _connection


def close_connection() -> None:
    global _connection
    if _connection is not None:
        _connection.close()
        _connection = None
