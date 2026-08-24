"""덤프 파일 파싱, 배치 실행 로직, DB 이름/확인 로직을 검증한다.

실제 psycopg2 DB 접속은 pytest로 검증하지 않는다(DB가 필요해서). 로컬
docker 환경에서 직접 실행해 검증한다.
"""

from pathlib import Path

from postgres_restore import (
    build_new_database_name,
    build_previous_database_name,
    build_swap_failure_message,
    parse_created_tables,
    restore_confirmed,
    restore_sql_file,
    target_database_exists,
)


class _FakeCursor:
    def __init__(self, row: tuple | None) -> None:
        self._row = row
        self.executed: list[str] = []

    def __enter__(self) -> "_FakeCursor":
        return self

    def __exit__(self, *exc_info: object) -> None:
        return None

    def execute(self, sql: str, params: tuple = ()) -> None:
        self.executed.append(sql)

    def fetchone(self) -> tuple | None:
        return self._row


class _FakeConnection:
    def __init__(self, cursor: _FakeCursor) -> None:
        self._cursor = cursor
        self.commit_count = 0

    def cursor(self) -> _FakeCursor:
        return self._cursor

    def commit(self) -> None:
        self.commit_count += 1


SAMPLE_SQL = """--
-- PostgreSQL database dump
--

\\restrict abc123

CREATE TABLE production.product (
    productid integer NOT NULL,
    name text
);

CREATE TABLE purchasing.vendor (
    businessentityid integer NOT NULL
);

INSERT INTO production.product VALUES (1, 'Adjustable Race');
INSERT INTO production.product VALUES (2, 'Bearing Ball; sold in pairs');

\\unrestrict abc123
"""


def test_parse_created_tables_extracts_schema_table_pairs(tmp_path: Path) -> None:
    sql_path = tmp_path / "dump.sql"
    sql_path.write_text(SAMPLE_SQL, encoding="utf-8")

    names = parse_created_tables(sql_path)

    assert names == {"production.product", "purchasing.vendor"}


def test_parse_created_tables_returns_empty_set_when_no_create_table(
    tmp_path: Path,
) -> None:
    sql_path = tmp_path / "dump.sql"
    sql_path.write_text("SELECT 1;\n", encoding="utf-8")

    assert parse_created_tables(sql_path) == set()


def test_restore_sql_file_executes_all_statements(tmp_path: Path) -> None:
    sql_path = tmp_path / "dump.sql"
    sql_path.write_text(SAMPLE_SQL, encoding="utf-8")
    cursor = _FakeCursor(None)
    conn = _FakeConnection(cursor)

    statement_count = restore_sql_file(conn, sql_path, batch_char_limit=1_000_000)

    # CREATE TABLE 2개 + INSERT 2개 = 4개 문장, 세미콜론이 줄 끝이 아닌
    # "Bearing Ball; sold in pairs" 데이터 안 세미콜론 때문에 잘못 나뉘지 않아야 한다.
    assert statement_count == 4


def test_restore_sql_file_skips_psql_meta_commands(tmp_path: Path) -> None:
    sql_path = tmp_path / "dump.sql"
    sql_path.write_text(SAMPLE_SQL, encoding="utf-8")
    cursor = _FakeCursor(None)
    conn = _FakeConnection(cursor)

    restore_sql_file(conn, sql_path, batch_char_limit=1_000_000)

    executed_text = "\n".join(cursor.executed)
    assert "\\restrict" not in executed_text
    assert "\\unrestrict" not in executed_text


def test_restore_sql_file_does_not_split_on_semicolon_inside_data(
    tmp_path: Path,
) -> None:
    sql_path = tmp_path / "dump.sql"
    sql_path.write_text(SAMPLE_SQL, encoding="utf-8")
    cursor = _FakeCursor(None)
    conn = _FakeConnection(cursor)

    restore_sql_file(conn, sql_path, batch_char_limit=1_000_000)

    executed_text = "\n".join(cursor.executed)
    assert "Bearing Ball; sold in pairs" in executed_text


def test_restore_sql_file_splits_into_multiple_batches_when_over_limit(
    tmp_path: Path,
) -> None:
    sql_path = tmp_path / "dump.sql"
    sql_path.write_text(SAMPLE_SQL, encoding="utf-8")
    cursor = _FakeCursor(None)
    conn = _FakeConnection(cursor)

    # 배치 한도를 아주 작게 잡아서 문장마다 배치가 나뉘도록 강제한다.
    restore_sql_file(conn, sql_path, batch_char_limit=1)

    assert len(cursor.executed) == 4
    assert conn.commit_count == 4


def test_target_database_exists_true_when_row_found() -> None:
    class _Conn:
        def cursor(self) -> _FakeCursor:
            return _FakeCursor((1,))

    assert target_database_exists(_Conn(), "adventureworks") is True


def test_target_database_exists_false_when_no_row() -> None:
    class _Conn:
        def cursor(self) -> _FakeCursor:
            return _FakeCursor(None)

    assert target_database_exists(_Conn(), "adventureworks") is False


def test_restore_confirmed_true_when_input_matches_db_name() -> None:
    assert restore_confirmed("adventureworks", "adventureworks") is True


def test_restore_confirmed_false_when_input_does_not_match() -> None:
    assert restore_confirmed("yes", "adventureworks") is False


def test_restore_confirmed_strips_surrounding_whitespace() -> None:
    assert restore_confirmed(" adventureworks \n", "adventureworks") is True


def test_restore_confirmed_false_for_empty_input() -> None:
    assert restore_confirmed("", "adventureworks") is False


def test_build_new_database_name_appends_restore_and_timestamp() -> None:
    assert (
        build_new_database_name("adventureworks", "20260820T120000Z")
        == "adventureworks_restore_20260820T120000Z"
    )


def test_build_previous_database_name_appends_previous_and_timestamp() -> None:
    assert (
        build_previous_database_name("adventureworks", "20260820T120000Z")
        == "adventureworks_previous_20260820T120000Z"
    )


def test_build_swap_failure_message_in_use_mentions_retry_swap_option() -> None:
    message = build_swap_failure_message(
        "in_use", db="adventureworks", new_db="adventureworks_restore_x"
    )

    assert "adventureworks" in message
    assert "adventureworks_restore_x" in message
    assert "--retry-swap" in message


def test_build_swap_failure_message_race_mentions_other_session() -> None:
    message = build_swap_failure_message(
        "race", db="adventureworks", new_db="adventureworks_restore_x"
    )

    assert "다른 세션" in message
    assert "adventureworks_restore_x" in message


def test_build_swap_failure_message_unknown_reason_falls_back() -> None:
    message = build_swap_failure_message(
        "mystery", db="adventureworks", new_db="adventureworks_restore_x"
    )

    assert "mystery" in message
