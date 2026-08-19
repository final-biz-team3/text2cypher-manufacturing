"""pg_restore 명령 조립과 TOC(목차) 파싱 로직을 검증한다.

실제 pg_restore 실행·DB 접속은 pytest로 검증하지 않는다(바이너리·DB가
필요해서). 로컬 docker 환경에서 직접 실행해 검증한다.
"""

from pathlib import Path

from restore_postgres import (
    build_pg_restore_command,
    build_pg_restore_list_command,
    parse_toc_table_names,
    target_database_exists,
    wrap_for_docker_exec,
)


class _FakeCursor:
    def __init__(self, row: tuple | None) -> None:
        self._row = row

    def __enter__(self) -> "_FakeCursor":
        return self

    def __exit__(self, *exc_info: object) -> None:
        return None

    def execute(self, sql: str, params: tuple) -> None:
        pass

    def fetchone(self) -> tuple | None:
        return self._row


def test_build_pg_restore_command_includes_connection_and_dump_path() -> None:
    dump_path = Path("etl/data/AdventureWorksPG.gz")

    command = build_pg_restore_command(
        dump_path,
        host="localhost",
        port="5432",
        db="adventureworks",
        user="postgres",
    )

    assert command[0] == "pg_restore"
    assert "--host=localhost" in command
    assert "--port=5432" in command
    assert "--dbname=adventureworks" in command
    assert "--username=postgres" in command
    assert "--no-owner" in command
    assert "--no-acl" in command
    assert "--clean" in command
    assert "--if-exists" in command
    assert command[-1] == str(dump_path)


def test_build_pg_restore_list_command_has_no_dbname() -> None:
    """--list는 DB 접속 없이 덤프 파일만 읽으므로 --dbname을 넣지 않는다."""
    dump_path = Path("etl/data/AdventureWorksPG.gz")

    command = build_pg_restore_list_command(dump_path)

    assert command == ["pg_restore", "--list", str(dump_path)]


SAMPLE_TOC = """;
; Archive created at 2026-08-19 12:00:00
;     dbname: adventureworks
;     TOC Entries: 6
;     Format: CUSTOM
;     Dumped from database version: 14.4
;
;
; Selected TOC Entries:
;
3521; 1259 16394 TABLE production product postgres
3522; 1259 16400 TABLE purchasing vendor postgres
3800; 0 16394 TABLE DATA production product postgres
3801; 0 16400 TABLE DATA purchasing vendor postgres
3802; 0 16410 TABLE DATA production workorder postgres
2600; 1259 16500 SEQUENCE production product_productid_seq postgres
"""


def test_parse_toc_table_names_extracts_table_data_entries_only() -> None:
    """TABLE(스키마 정의)이 아니라 TABLE DATA(실제 데이터 적재 대상) 행만 뽑는다 -
    데이터가 없는 빈 테이블은 유실 검증 대상에서 의미가 없다."""
    names = parse_toc_table_names(SAMPLE_TOC)

    assert names == {
        "production.product",
        "purchasing.vendor",
        "production.workorder",
    }


def test_parse_toc_table_names_ignores_sequences_and_comments() -> None:
    names = parse_toc_table_names(SAMPLE_TOC)

    assert "production.product_productid_seq" not in names
    assert len(names) == 3


def test_wrap_for_docker_exec_prefixes_docker_exec_with_container_and_env() -> None:
    """로컬에 pg_restore가 없을 때, 컨테이너 안의 pg_restore를 대신 부르도록
    명령 앞에 docker exec를 붙인다. env는 -e로 하나씩 넘긴다(호스트
    환경변수가 컨테이너 안으로 자동으로 안 들어가므로 명시적으로 전달)."""
    command = ["pg_restore", "--list", "/tmp/AdventureWorksPG.gz"]

    wrapped = wrap_for_docker_exec(
        command, container="postgres", env={"PGPASSWORD": "secret"}
    )

    assert wrapped == [
        "docker",
        "exec",
        "-e",
        "PGPASSWORD=secret",
        "-i",
        "postgres",
        "pg_restore",
        "--list",
        "/tmp/AdventureWorksPG.gz",
    ]


def test_wrap_for_docker_exec_with_no_env_omits_dash_e() -> None:
    command = ["pg_restore", "--list", "/tmp/AdventureWorksPG.gz"]

    wrapped = wrap_for_docker_exec(command, container="postgres", env={})

    assert wrapped == ["docker", "exec", "-i", "postgres", *command]


def test_build_pg_restore_command_preserves_forward_slashes_for_container_paths() -> (
    None
):
    """컨테이너 안(Linux) 경로는 str로 그대로 넘겨야 한다 - Windows에서
    pathlib.Path("/tmp/x")로 감싸면 "/"가 "\\"로 바뀌어 컨테이너 안에서
    파일을 못 찾는 버그가 실제로 있었다(2026-08-20 실행 검증에서 발견).
    이 테스트는 str을 그대로 넘겼을 때 슬래시가 안 바뀌는지 확인한다 -
    누군가 다시 Path()로 감싸는 회귀를 방지한다."""
    command = build_pg_restore_command(
        "/tmp/AdventureWorksPG.gz",
        host="localhost",
        port="5432",
        db="adventureworks",
        user="postgres",
    )

    assert command[-1] == "/tmp/AdventureWorksPG.gz"


def test_build_pg_restore_list_command_preserves_forward_slashes_for_container_paths() -> (
    None
):
    command = build_pg_restore_list_command("/tmp/AdventureWorksPG.gz")

    assert command == ["pg_restore", "--list", "/tmp/AdventureWorksPG.gz"]


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
