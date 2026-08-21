"""AdventureWorksPG.sql(순수 SQL, INSERT 문 기반 pg_dump plain 포맷)을
psycopg2만으로 복원한다 - pg_restore/psql/Docker 등 외부 프로그램이 전혀
필요 없다.

기존에는 custom format 덤프(AdventureWorksPG.gz)를 `pg_restore` 바이너리로
복원했다. 이 바이너리는 Windows에 기본으로 없어서 로컬 docker-compose의
postgres 컨테이너 안 것을 `docker exec`로 빌려 썼는데, 이게 "설치 없이
`pip install -r requirements.txt`만으로 바로 작동해야 한다"는 이 프로젝트의
목표와 안 맞는다는 지적을 받았다(2026-08-21) - Docker Desktop을 켜두고
컨테이너 상태까지 신경 써야 하는 건 목표가 아니다.

해결: 덤프를 plain SQL(`pg_dump --format=plain --inserts`)로 한 번(이 저장소
관리자가 로컬에서) 다시 뽑아서 커밋 대신 `etl/data/`에 배포한다. 이 형식은
스키마 정의(CREATE TABLE 등)와 데이터(INSERT INTO ... VALUES (...))가 전부
순수 SQL 텍스트라 `psycopg2.cursor.execute()`만으로 실행할 수 있다 - COPY
블록이 없어서(`--inserts`) 별도 스트리밍 처리도 필요 없다. pg_dump가 파일
맨 위/아래에 남기는 `\\restrict`/`\\unrestrict` 같은 psql 전용 메타 명령(백슬래시로
시작하는 줄)은 SQL이 아니므로 건너뛴다.

파일 전체(약 140MB)를 한 번에 실행하면 PostgreSQL이 그 쿼리 문자열을 담을
공유 메모리를 못 잡아 실패한다("could not resize shared memory segment",
로컬 docker 컨테이너 /dev/shm 64MB 환경에서 실행 검증 중 발견, 2026-08-21).
그래서 문장을 몇 MB 단위 배치로 나눠 실행한다(restore_sql_file).

전체 흐름은 기존과 동일하게 유지한다: 기존 DB에 바로 덮어쓰지 않고, 항상 새
DB(`{db}_restore_<타임스탬프>`)를 만들어 그쪽에 복원·검증하고, 검증까지
통과한 뒤에만 기존 DB와 이름을 교체한다 - 기존 DB는 지우지 않고
`{db}_previous_<타임스탬프>`로 보존한다(사람이 확인 후 직접 정리). 교체
직전에는 로컬/원격 구분 없이 항상 대상 DB 이름을 그대로 입력하는 확인
절차를 거친다 - 호스트 문자열만으로 로컬/원격을 구분해서 로컬이면 확인을
건너뛰는 방식은 안전하지 않다고 판단했다(SSH 터널을 쓰면 원격도
"localhost"로 보이고, 누군가의 로컬 DB가 다른 사람에게는 실제로 공유
서버일 수도 있다). 자동화/CI에서 쓸 때는 --yes를 매번 명시적으로 넘겨 이
확인을 생략할 수 있다 - .env에 영구 저장하는 방식은 이번 사고 시나리오
(설정을 깜빡하고 안 되돌림)를 그대로 재현하므로 쓰지 않는다.

교체 자체(ALTER DATABASE ... RENAME TO ...)는 대상 DB에 다른 활성 연결이
하나라도 있으면 PostgreSQL이 자동으로 실패시킨다 - 그래서 강제로 연결을
끊지 않아도 안전하게 자동화할 수 있다(로컬 테스트로 실제 확인,
2026-08-20). 두 RENAME을 한 트랜잭션으로 묶어서, 실패하면 아무 것도 안
바뀐 채로 롤백되고 새로 복원된 DB는 그대로 남는다 - 나중에 다시
시도하거나 사람이 직접 처리하면 된다.
"""

import argparse
import json
import os
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

import psycopg2
import psycopg2.errors
from dotenv import load_dotenv
from psycopg2 import sql

ROOT_DIR = Path(__file__).resolve().parent.parent
REQUIRED_ENV_VARS = ["POSTGRES_HOST", "POSTGRES_PORT", "POSTGRES_DB", "POSTGRES_USER"]

# 배치 하나당 문장 텍스트 총 길이 상한(문자 수). PostgreSQL이 큰 멀티 문장
# 쿼리 문자열을 담을 공유 메모리를 못 잡는 문제(로컬 docker /dev/shm 64MB
# 환경에서 실측)를 피하기 위해 여유 있게 2MB로 잡았다 - 실제 AdventureWorks
# 전체(약 76만 문장, 140MB)도 70개 배치로 25초 안에 끝나는 것을 확인했다.
BATCH_CHAR_LIMIT = 2_000_000

CREATE_TABLE_LINE = re.compile(r"^CREATE TABLE (\S+)\.(\S+) \($")


def parse_created_tables(sql_text: str) -> set[str]:
    """plain SQL 덤프 텍스트에서 CREATE TABLE로 만들어질 테이블 이름을 뽑는다.

    반환값은 "schema.table" 형태의 집합이며, 복원 후 실제 DB에 같은 테이블이
    존재하는지 사후 검증(postgres_restore_validate.py)에서 대조하는 기준이 된다.
    pg_dump --format=plain 출력은 "CREATE TABLE schema.table (" 형태로 한
    줄에 딱 한 번씩 남기므로 정규식 하나로 충분하다.
    """
    tables: set[str] = set()
    for line in sql_text.splitlines():
        match = CREATE_TABLE_LINE.match(line)
        if match:
            schema, table = match.groups()
            tables.add(f"{schema}.{table}")
    return tables


def restore_sql_file(
    conn, sql_path: Path, *, batch_char_limit: int = BATCH_CHAR_LIMIT
) -> int:
    """plain SQL 덤프 파일을 psycopg2만으로 배치 실행한다.

    한 줄이 세미콜론으로 끝나는 지점을 문장 경계로 본다(pg_dump plain 출력
    관례 - CREATE TABLE처럼 여러 줄에 걸친 문장도 마지막 줄만 세미콜론으로
    끝나므로 그때까지 누적한다). 데이터 안에 세미콜론이 있어도(예: 텍스트
    필드 안 문장 부호) 줄 끝이 아니면 문장 경계로 오인하지 않는다(실제
    AdventureWorks 덤프 76만 줄 기준 검증 완료). `\\`로 시작하는 psql 전용
    메타 명령(`\\restrict` 등)은 SQL이 아니므로 건너뛴다.

    반환값: 실행한 문장 개수.
    """
    buffer_lines: list[str] = []
    batch: list[str] = []
    batch_len = 0
    statement_count = 0

    with sql_path.open(encoding="utf-8") as f, conn.cursor() as cursor:
        for line in f:
            if line.startswith("\\"):
                continue
            buffer_lines.append(line)
            if not line.rstrip().endswith(";"):
                continue
            statement = "".join(buffer_lines)
            buffer_lines = []
            statement_count += 1
            batch.append(statement)
            batch_len += len(statement)
            if batch_len >= batch_char_limit:
                cursor.execute("".join(batch))
                conn.commit()
                batch = []
                batch_len = 0
        if batch:
            cursor.execute("".join(batch))
            conn.commit()

    return statement_count


def restore_confirmed(user_input: str, db: str) -> bool:
    """사용자가 입력한 문자열이 대상 DB 이름과 정확히 일치하는지 확인한다.

    --clean 복원은 로컬/원격을 가리지 않고 항상 이 확인을 거친다(PR #16 리뷰
    P1-2 대응). 처음엔 "원격일 때만 확인"을 고려했으나, 호스트 문자열만으로는
    로컬/원격을 안전하게 구분할 수 없다(SSH 터널을 쓰면 원격도 "localhost"로
    보이고, 누군가의 로컬 DB가 다른 사람에게는 공유 서버일 수도 있다) - 그래서
    대상 구분 없이 매번 DB 이름을 그대로 입력해야만 진행되게 한다.
    """
    return user_input.strip() == db


def target_database_exists(conn, db: str) -> bool:
    """conn(서버의 postgres 유지보수 DB 연결)으로 db가 이미 존재하는지 확인한다.

    로컬에 DB가 있다고 가정하지 않는다 - .env가 가리키는 서버에 실제로
    접속해서 확인하고, 없으면 복원을 시도하지 않고 바로 알린다.
    """
    with conn.cursor() as cursor:
        cursor.execute("SELECT 1 FROM pg_database WHERE datname = %s", (db,))
        return cursor.fetchone() is not None


def generate_restore_timestamp() -> str:
    """이번 복원 실행을 식별할 타임스탬프를 만든다. 새 DB 이름과 보존용 이름에
    같이 쓰여서, DB 목록만 보고도 어느 복원 실행이 만든 것인지 알 수 있다."""
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def build_new_database_name(db: str, timestamp: str) -> str:
    """복원을 받을 새 임시 DB 이름을 만든다."""
    return f"{db}_restore_{timestamp}"


def build_previous_database_name(db: str, timestamp: str) -> str:
    """교체 전 기존 DB를 보존할 이름을 만든다."""
    return f"{db}_previous_{timestamp}"


def create_database(conn, db_name: str) -> None:
    """conn(유지보수 DB 연결)으로 새 DB를 만든다.

    CREATE DATABASE는 트랜잭션 안에서 실행할 수 없으므로 conn.autocommit이
    True여야 한다(호출자 책임).
    """
    with conn.cursor() as cursor:
        cursor.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(db_name)))


def swap_databases(
    conn, *, live_db: str, new_db: str, previous_db_name: str | None
) -> bool:
    """new_db를 live_db 이름으로 승격한다(기존 live_db가 있으면 previous_db_name으로 보존).

    두 RENAME을 한 트랜잭션으로 묶어서 실행한다(conn.autocommit=False여야
    함, 호출자 책임) - 대상 DB에 다른 활성 연결이 있으면 PostgreSQL이
    ObjectInUse로 문장 실행 자체를 실패시키므로, 강제로 연결을 끊지 않아도
    "지금은 안 됨"으로 안전하게 끝난다.

    반환값: 성공하면 True. 다른 세션이 사용 중이라 실패했으면(ObjectInUse)
    롤백하고 False를 반환한다(호출자가 재시도를 안내하도록) - 이 경우
    live_db·new_db 둘 다 그대로 보존된다. 그 외 예상 못 한 오류는 그대로
    예외로 전파한다.
    """
    try:
        with conn.cursor() as cursor:
            if previous_db_name is not None:
                cursor.execute(
                    sql.SQL("ALTER DATABASE {} RENAME TO {}").format(
                        sql.Identifier(live_db), sql.Identifier(previous_db_name)
                    )
                )
            cursor.execute(
                sql.SQL("ALTER DATABASE {} RENAME TO {}").format(
                    sql.Identifier(new_db), sql.Identifier(live_db)
                )
            )
        conn.commit()
        return True
    except psycopg2.errors.ObjectInUse:
        conn.rollback()
        return False


def main() -> None:
    load_dotenv(ROOT_DIR / ".env")

    parser = argparse.ArgumentParser(
        description="AdventureWorksPG.sql을 PostgreSQL로 복원한다."
    )
    parser.add_argument("--dump-path", default="etl/data/AdventureWorksPG.sql")
    parser.add_argument(
        "--yes",
        action="store_true",
        help="확인 프롬프트 없이 진행한다(자동화/CI 전용, 매번 명시적으로 넘겨야 함).",
    )
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

    print("0) 덤프 파일 확인 (사전 검증)")
    sql_text = dump_path.read_text(encoding="utf-8")
    expected_tables = parse_created_tables(sql_text)
    if not expected_tables:
        sys.exit(
            "덤프 파일에서 CREATE TABLE 문을 하나도 찾지 못했습니다 - "
            "파일이 손상됐거나 잘못된 파일일 수 있습니다."
        )
    print(f"   덤프에 정의된 테이블 {len(expected_tables)}개 확인")

    timestamp = generate_restore_timestamp()
    new_db = build_new_database_name(db, timestamp)

    maintenance_conn = psycopg2.connect(
        host=host, port=port, dbname="postgres", user=user, password=password
    )
    maintenance_conn.autocommit = True
    live_db_exists = target_database_exists(maintenance_conn, db)
    print(f"1) 새 DB '{new_db}' 생성 (기존 '{db}'는 아직 건드리지 않음)")
    create_database(maintenance_conn, new_db)

    print(f"2) 복원 실행 (새 DB '{new_db}', 기존 '{db}'는 그대로)")
    restore_conn = psycopg2.connect(
        host=host, port=port, dbname=new_db, user=user, password=password
    )
    try:
        statement_count = restore_sql_file(restore_conn, dump_path)
    except Exception as exc:
        restore_conn.close()
        sys.exit(
            f"복원 실패 ({exc}) - 새 DB '{new_db}'는 조사를 위해 남겨뒀습니다. "
            "확인 후 필요 없으면 직접 삭제하세요."
        )
    print(f"   복원 완료 (문장 {statement_count}개 실행, 테이블 {len(expected_tables)}개)")

    print(f"3) 사후 검증 (새 DB '{new_db}' 대상, 테이블 존재 + 픽스처 값 대조)")
    from postgres_restore_validate import (
        build_fixture_checks,
        find_missing_tables,
        run_fixture_checks,
    )

    parameters_path = ROOT_DIR / "queries" / "query_parameters.json"

    try:
        missing_tables = find_missing_tables(expected_tables, restore_conn)
        entities = json.loads(parameters_path.read_text(encoding="utf-8"))["entities"]
        failures = run_fixture_checks(restore_conn, build_fixture_checks(entities))
    finally:
        restore_conn.close()

    if missing_tables:
        print(f"   누락된 테이블 {len(missing_tables)}개: {sorted(missing_tables)}")
    if failures:
        print(f"   픽스처 불일치 {len(failures)}건:")
        for failure in failures:
            print(f"     - {failure}")

    if missing_tables or failures:
        sys.exit(
            f"검증 실패 - 새 DB '{new_db}'는 조사를 위해 남겨뒀습니다. 확인 후 "
            "필요 없으면 직접 삭제하세요."
        )
    print(f"   테이블 {len(expected_tables)}개 전체 확인, 픽스처 유실/손상 없음")

    print(f"4) '{db}' <- '{new_db}' 교체")
    previous_db_name = build_previous_database_name(db, timestamp) if live_db_exists else None
    if previous_db_name is not None:
        print(f"   기존 '{db}'는 지우지 않고 '{previous_db_name}'로 보존합니다.")
    else:
        print(f"   '{db}'가 아직 없어서 새로 만듭니다.")
    if args.yes:
        print("   (--yes로 확인 생략)")
    else:
        user_input = input(f"계속하려면 데이터베이스 이름을 그대로 입력하세요 [{db}]: ")
        if not restore_confirmed(user_input, db):
            sys.exit(
                "입력한 이름이 일치하지 않아 교체를 중단합니다. 복원된 데이터는 "
                f"'{new_db}'에 그대로 남아있습니다."
            )

    maintenance_conn.autocommit = False
    swapped = swap_databases(
        maintenance_conn, live_db=db, new_db=new_db, previous_db_name=previous_db_name
    )
    maintenance_conn.close()

    if not swapped:
        sys.exit(
            f"교체 실패 - 다른 세션이 '{db}' 또는 '{new_db}'에 접속 중입니다. "
            f"복원된 데이터는 '{new_db}'에 안전하게 남아있으니, 나중에 다시 "
            "실행하거나 직접 ALTER DATABASE로 교체하세요."
        )

    print(f"   교체 완료. '{db}'가 이번에 복원한 데이터를 가리킵니다.")
    if previous_db_name is not None:
        print(
            f"   기존 데이터는 '{previous_db_name}'로 보존됨 - 확인 후 필요 없으면 "
            "직접 삭제하세요."
        )


if __name__ == "__main__":
    main()
