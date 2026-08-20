"""AdventureWorksPG.gz(PostgreSQL 커스텀 포맷 덤프)를 pg_restore로 복원한다.

pg_restore는 postgres:16 공식 Docker 이미지에 이미 포함돼 있으므로 별도
설치가 필요 없다. 다만 이 프로젝트의 개발 환경(Windows)엔 pg_restore가
PATH에 없는 경우가 있다 - 그럴 땐 로컬 docker-compose의 postgres 컨테이너
안에 이미 들어있는 pg_restore를 `docker exec`로 대신 부른다(자동 판단,
PG_RESTORE_DOCKER_CONTAINER 환경변수로 컨테이너 이름을 바꿀 수 있음,
기본값 "postgres"). --host/--port는 원격 공유 서버를 그대로 가리킬 수
있으므로, 컨테이너를 거치더라도 실제 복원 대상은 로컬이든 원격이든 상관없다.

복원 전 `--list`로 덤프의 목차(TOC)를 먼저 읽어 몇 개 테이블에 데이터가
있는지 확인하고(사전 검증), 복원 후에는 postgres_restore_validate.py의
검증 함수를 그대로 불러와 테이블·픽스처 값 사후 검증까지 이어서 실행한다.
검증 로직 자체는 psycopg2가 필요해 이 파일과 책임을 분리해 두고, 이 파일의
main()에서만 두 단계를 이어붙인다 - 복원 없이 이미 있는 DB만 검증하고
싶을 때는 postgres_restore_validate.py를 그대로 독립 실행하면 된다.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import psycopg2
from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parent.parent
REQUIRED_ENV_VARS = ["POSTGRES_HOST", "POSTGRES_PORT", "POSTGRES_DB", "POSTGRES_USER"]

# "TABLE DATA <schema> <table> <owner>" 형태의 TOC 행만 잡는다. "TABLE"(스키마
# 정의)이나 "SEQUENCE" 등 데이터가 없는 항목은 유실 검증에 의미가 없어 제외한다.
TABLE_DATA_LINE = re.compile(r"^\d+;\s+0\s+\d+\s+TABLE DATA\s+(\S+)\s+(\S+)\s+\S+$")


def build_pg_restore_command(
    dump_path: Path | str,
    *,
    host: str,
    port: str,
    db: str,
    user: str,
) -> list[str]:
    """pg_restore 실행 인자 목록을 조립한다.

    dump_path는 Path 대신 str로 넘길 수도 있다 - docker exec로 컨테이너
    안(Linux)의 파일을 가리킬 때(예: "/tmp/x.gz") Windows에서 pathlib.Path로
    감싸면 WindowsPath가 "/"를 "\\"로 바꿔버려 컨테이너 안에서 경로를 못
    찾는 버그가 있었다(실행 검증에서 발견, 2026-08-20). 컨테이너 경로는
    항상 str 그대로 넘긴다.

    --clean --if-exists: 재실행해도 안전하도록 기존 객체를 먼저 지우고 복원한다.
    --no-owner: 원본 덤프의 소유자(Windows 로컬 계정)를 무시하고 접속 계정
    소유로 복원한다(대상 서버 계정이 원본과 다를 수 있어 반드시 필요).
    --no-acl: 원본이 Azure Database for PostgreSQL에서 뜬 덤프라 GRANT 대상에
    azure_pg_admin 같은 Azure 전용 역할이 섞여 있다. 이 역할은 우리 환경(로컬
    docker, 원격 공유 서버)에 존재하지 않아 권한 부여문마다 오류가 난다(실행
    검증 결과 76건, 전부 이 원인). 우리는 자체 접속 계정으로 이미 전체 권한을
    가지므로 원본 권한을 복원할 필요가 없어 통째로 건너뛴다 - 매번 반복되는
    무해한 오류를 로그에 남기지 않기 위해서다.
    """
    return [
        "pg_restore",
        f"--host={host}",
        f"--port={port}",
        f"--dbname={db}",
        f"--username={user}",
        "--no-owner",
        "--no-acl",
        "--clean",
        "--if-exists",
        "--verbose",
        str(dump_path),
    ]


def build_pg_restore_list_command(dump_path: Path | str) -> list[str]:
    """덤프 파일의 목차(TOC)만 읽는 명령을 조립한다. DB 접속이 필요 없다."""
    return ["pg_restore", "--list", str(dump_path)]


def parse_toc_table_names(toc_text: str) -> set[str]:
    """`pg_restore --list` 출력에서 실제 데이터가 있는 테이블 이름을 뽑는다.

    반환값은 "schema.table" 형태의 집합이며, 복원 후 실제 DB에 같은 테이블이
    존재하는지 사후 검증(postgres_restore_validate.py)에서 대조하는 기준이 된다.
    """
    tables: set[str] = set()
    for line in toc_text.splitlines():
        match = TABLE_DATA_LINE.match(line.strip())
        if match:
            schema, table = match.groups()
            tables.add(f"{schema}.{table}")
    return tables


def wrap_for_docker_exec(
    command: list[str], *, container: str, env: dict[str, str]
) -> list[str]:
    """로컬에 없는 pg_restore 대신 컨테이너 안의 pg_restore를 부르도록 감싼다.

    호스트 환경변수는 컨테이너 안으로 자동으로 안 들어가므로(PGPASSWORD 등)
    -e로 하나씩 명시적으로 전달한다.
    """
    docker_command = ["docker", "exec"]
    for key, value in env.items():
        docker_command += ["-e", f"{key}={value}"]
    docker_command += ["-i", container]
    return docker_command + command


def target_database_exists(conn, db: str) -> bool:
    """conn(서버의 postgres 유지보수 DB 연결)으로 db가 이미 존재하는지 확인한다.

    로컬에 DB가 있다고 가정하지 않는다 - .env가 가리키는 서버에 실제로
    접속해서 확인하고, 없으면 복원을 시도하지 않고 바로 알린다.
    """
    with conn.cursor() as cursor:
        cursor.execute("SELECT 1 FROM pg_database WHERE datname = %s", (db,))
        return cursor.fetchone() is not None


def copy_dump_into_container(
    dump_path: Path, *, container: str, container_path: str
) -> None:
    """호스트의 덤프 파일을 컨테이너 안의 container_path로 복사한다.

    --list(TOC 조회)는 스트림이 아니라 파일 임의 접근이 필요해 stdin 파이프로
    못 넘기므로, 컨테이너 안에 실제 파일로 먼저 넣어둔다.
    """
    subprocess.run(
        ["docker", "cp", str(dump_path), f"{container}:{container_path}"],
        check=True,
    )


def main() -> None:
    load_dotenv(ROOT_DIR / ".env")

    parser = argparse.ArgumentParser(
        description="AdventureWorksPG.gz를 PostgreSQL로 복원한다."
    )
    parser.add_argument("--dump-path", default="etl/data/AdventureWorksPG.gz")
    args = parser.parse_args()

    dump_path = Path(args.dump_path)
    if not dump_path.exists():
        sys.exit(f"덤프 파일이 없습니다: {dump_path}")

    missing_vars = [name for name in REQUIRED_ENV_VARS if not os.environ.get(name)]
    if missing_vars:
        sys.exit(f".env에 다음 값이 없습니다: {', '.join(missing_vars)}")

    host = os.environ["POSTGRES_HOST"]
    port = os.environ["POSTGRES_PORT"]
    db = os.environ["POSTGRES_DB"]
    user = os.environ["POSTGRES_USER"]
    password = os.environ.get("POSTGRES_PASSWORD", "")
    print(f"대상: {host}:{port}/{db}")

    maintenance_conn = psycopg2.connect(
        host=host, port=port, dbname="postgres", user=user, password=password
    )
    try:
        if not target_database_exists(maintenance_conn, db):
            sys.exit(
                f"'{db}' 데이터베이스가 {host}:{port}에 없습니다. 먼저 만들어야 합니다."
            )
    finally:
        maintenance_conn.close()

    via_docker = shutil.which("pg_restore") is None
    container = os.environ.get("PG_RESTORE_DOCKER_CONTAINER", "postgres")
    if via_docker:
        container_dump_path = f"/tmp/{dump_path.name}"
        print(f"   (로컬에 pg_restore가 없어 컨테이너 '{container}' 안에서 실행)")
        copy_dump_into_container(
            dump_path, container=container, container_path=container_dump_path
        )
        # 컨테이너 안(Linux) 경로이므로 Path로 감싸지 않는다 - Windows에서
        # Path("/tmp/x")는 "/"를 "\"로 바꿔버려 컨테이너 안에서 못 찾는다.
        restore_target_path: Path | str = container_dump_path
    else:
        restore_target_path = dump_path

    print("1) 덤프 목차(TOC) 확인 (사전 검증)")
    list_command = build_pg_restore_list_command(restore_target_path)
    if via_docker:
        list_command = wrap_for_docker_exec(list_command, container=container, env={})
    list_result = subprocess.run(
        list_command, capture_output=True, text=True, check=False
    )
    expected_tables = parse_toc_table_names(list_result.stdout)
    print(f"   덤프에 데이터가 있는 테이블 {len(expected_tables)}개 확인")

    print("2) 복원 실행")
    command = build_pg_restore_command(
        restore_target_path, host=host, port=port, db=db, user=user
    )
    if via_docker:
        command = wrap_for_docker_exec(
            command, container=container, env={"PGPASSWORD": password}
        )
        env = os.environ
    else:
        env = {**os.environ, "PGPASSWORD": password}
    result = subprocess.run(command, env=env, check=False)
    if result.returncode != 0:
        sys.exit(f"pg_restore 실패 (exit code {result.returncode})")
    print(f"   복원 완료 (기대 테이블 수: {len(expected_tables)})")

    print("3) 사후 검증 (테이블 존재 + 픽스처 값 대조)")
    from postgres_restore_validate import (
        build_fixture_checks,
        find_missing_tables,
        run_fixture_checks,
    )

    parameters_path = ROOT_DIR / "queries" / "reference" / "query_parameters.json"

    conn = psycopg2.connect(
        host=host, port=port, dbname=db, user=user, password=password
    )
    try:
        missing_tables = find_missing_tables(expected_tables, conn)
        entities = json.loads(parameters_path.read_text(encoding="utf-8"))["entities"]
        failures = run_fixture_checks(conn, build_fixture_checks(entities))
    finally:
        conn.close()

    if missing_tables:
        print(f"   누락된 테이블 {len(missing_tables)}개: {sorted(missing_tables)}")
    if failures:
        print(f"   픽스처 불일치 {len(failures)}건:")
        for failure in failures:
            print(f"     - {failure}")

    if missing_tables or failures:
        sys.exit(1)
    print(f"   테이블 {len(expected_tables)}개 전체 확인, 픽스처 유실/손상 없음")


if __name__ == "__main__":
    main()
