"""AdventureWorksPG.sql(순수 SQL, INSERT 문 기반 pg_dump plain 포맷)을
psycopg2만으로 복원한다 - pg_restore/psql/Docker 등 외부 프로그램이 전혀
필요 없다.

덤프는 plain SQL(`pg_dump --format=plain --inserts`) 형식으로 `etl/data/`에
배포된다. 스키마 정의(CREATE TABLE 등)와 데이터(INSERT INTO ... VALUES (...))가
전부 순수 SQL 텍스트라 `psycopg2.cursor.execute()`만으로 실행할 수 있다 - COPY
블록이 없어서(`--inserts`) 별도 스트리밍 처리도 필요 없다. pg_dump가 파일
맨 위/아래에 남기는 `\\restrict`/`\\unrestrict` 같은 psql 전용 메타 명령(백슬래시로
시작하는 줄)은 SQL이 아니므로 건너뛴다.

파일 전체(약 140MB)를 한 번에 실행하면 PostgreSQL이 그 쿼리 문자열을 담을
공유 메모리를 못 잡아 실패할 수 있다(예: `/dev/shm` 용량이 작은 환경에서
"could not resize shared memory segment"). 그래서 문장을 몇 MB 단위 배치로
나눠 실행한다(restore_sql_file).

기존 DB에 바로 덮어쓰지 않고, 항상 새 DB(`{db}_restore_<타임스탬프>`)를 만들어
그쪽에 복원·검증하고, 검증까지 통과한 뒤에만 기존 DB와 이름을 교체한다 - 기존
DB는 지우지 않고 `{db}_previous_<타임스탬프>`로 보존한다(사람이 확인 후 직접
정리). 교체 직전에는 로컬/원격 구분 없이 항상 대상 DB 이름을 그대로 입력하는
확인 절차를 거친다 - 호스트 문자열만으로는 로컬/원격을 안전하게 구분할 수
없기 때문이다(SSH 터널을 쓰면 원격도 "localhost"로 보이고, 누군가의 로컬 DB가
다른 사람에게는 실제로 공유 서버일 수도 있다). 자동화/CI에서 쓸 때는 --yes를
매번 명시적으로 넘겨 이 확인을 생략할 수 있다 - .env에 영구 저장하지 않는
이유는, 설정을 깜빡하고 안 되돌리면 이 확인 자체가 무력화되기 때문이다.

교체 자체(ALTER DATABASE ... RENAME TO ...)는 대상 DB에 다른 활성 연결이
하나라도 있으면 PostgreSQL이 자동으로 실패시킨다 - 그래서 강제로 연결을
끊지 않아도 안전하게 자동화할 수 있다. 두 RENAME을 한 트랜잭션으로 묶어서,
실패하면 아무 것도 안 바뀐 채로 롤백되고 새로 복원된 DB는 그대로 남는다 -
나중에 다시 시도하거나 사람이 직접 처리하면 된다.

이 "활성 연결이 있으면 거부"라는 안전장치는, 진행 중인 작업이 없는 idle
연결(예: 접속만 열어두고 있는 관리 도구)까지 막아버릴 수 있다. 그래서 교체
직전에 이름이 바뀌는 양쪽 DB(live_db·new_db 둘 다 - RENAME은 두 이름 모두
활성 연결이 없어야 한다) 각각의 idle 연결만 찾아서(활성 트랜잭션이 있는
연결은 절대 건드리지 않음) 종료할지 물어보고(--yes면 자동 진행), 그래도
실패하면(활성 연결이 있거나 동시에 다른 세션이 먼저 교체를 끝낸 경우) 사유를
구분해서 알려준다.
복원·검증까지는 끝났는데 교체만 실패한 경우 처음부터 다시 복원할 필요 없이
`--retry-swap <새 DB 이름>`으로 교체 단계만 재시도할 수 있다 - 이 로직(idle
정리, 확인, 교체, 실패 메시지)은 정상 흐름의 4단계와 --retry-swap 둘 다
retry_swap() 함수 하나를 공유해서 두 경로가 어긋나지 않게 했다.
"""

import argparse
import os
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

import psycopg2
import psycopg2.errors
from dotenv import load_dotenv
from paths import ROOT_DIR, load_fixture_entities
from psycopg2 import sql

REQUIRED_ENV_VARS = ["POSTGRES_HOST", "POSTGRES_PORT", "POSTGRES_DB", "POSTGRES_USER"]

# 배치 하나당 문장 텍스트 총 길이 상한(문자 수). PostgreSQL이 큰 멀티 문장
# 쿼리 문자열을 담을 공유 메모리(/dev/shm)를 못 잡는 문제를 피하기 위해
# 여유 있게 2MB로 잡았다.
BATCH_CHAR_LIMIT = 2_000_000

CREATE_TABLE_LINE = re.compile(r"^CREATE TABLE (\S+)\.(\S+) \($")


def parse_created_tables(sql_path: Path) -> set[str]:
    """plain SQL 덤프 파일에서 CREATE TABLE로 만들어질 테이블 이름을 뽑는다.

    파일 전체를 메모리에 올리지 않고 한 줄씩 읽는다(restore_sql_file()과 같은
    이유 - 덤프가 커도 안전하게 처리하기 위함). 반환값은 "schema.table" 형태의
    집합이며, 복원 후 실제 DB에 같은 테이블이 존재하는지 사후 검증
    (postgres_restore_validate.py)에서 대조하는 기준이 된다. pg_dump
    --format=plain 출력은 "CREATE TABLE schema.table (" 형태로 한 줄에 딱
    한 번씩 남기므로 정규식 하나로 충분하다.
    """
    tables: set[str] = set()
    with sql_path.open(encoding="utf-8") as f:
        for line in f:
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
    끝나므로 그때까지 누적한다). 데이터 안에 세미콜론이 있어도(예: "Bearing
    Ball; sold in pairs" 같은 텍스트 필드 안 문장 부호) 줄 끝이 아니면 문장
    경계로 오인하지 않는다. `\\`로 시작하는 psql 전용 메타 명령(`\\restrict` 등)은
    SQL이 아니므로 건너뛴다.

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

    복원은 로컬/원격을 가리지 않고 항상 이 확인을 거친다 - 호스트 문자열만
    보고 로컬/원격을 안전하게 구분할 수 없기 때문이다(SSH 터널을 쓰면 원격도
    "localhost"로 보이고, 누군가의 로컬 DB가 다른 사람에게는 공유 서버일
    수도 있다). 그래서 대상 구분 없이 매번 DB 이름을 그대로 입력해야만
    진행되게 한다.
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
) -> str:
    """new_db를 live_db 이름으로 승격한다(기존 live_db가 있으면 previous_db_name으로 보존).

    두 RENAME을 한 트랜잭션으로 묶어서 실행한다(conn.autocommit=False여야
    함, 호출자 책임) - 대상 DB에 다른 활성 연결이 있으면 PostgreSQL이
    ObjectInUse로 문장 실행 자체를 실패시키므로, 강제로 연결을 끊지 않아도
    안전하게 거부된다.

    반환값(문자열로 사유를 구분 - 호출자가 서로 다른 안내를 보여줄 수 있게):
    - "ok": 성공.
    - "in_use": 대상 DB에 활성 연결이 있어 실패(ObjectInUse). 롤백하고
      live_db·new_db 둘 다 그대로 보존된다.
    - "race": 그 사이 다른 세션이 먼저 교체를 끝내서 이름 상태가 예상과
      달라짐(InvalidCatalogName: live_db가 이미 이름이 바뀌어 없음 /
      DuplicateDatabase: live_db 이름이 이미 다른 DB가 차지함). PostgreSQL
      RENAME엔 원자적 충돌 감지가 없어서 이 두 예외로 간접 판별한다.

    그 외 예상 못 한 오류는 그대로 예외로 전파한다.
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
        return "ok"
    except psycopg2.errors.ObjectInUse:
        conn.rollback()
        return "in_use"
    except (psycopg2.errors.InvalidCatalogName, psycopg2.errors.DuplicateDatabase):
        conn.rollback()
        return "race"


def build_swap_failure_message(reason: str, *, db: str, new_db: str) -> str:
    """swap_databases()의 실패 사유(reason)에 맞는 안내 메시지를 만든다."""
    if reason == "in_use":
        return (
            f"교체 실패 - '{db}' 또는 '{new_db}'에서 활성 연결(트랜잭션 등)이 "
            f"사용 중이라 안전하게 교체할 수 없습니다. 복원된 데이터는 "
            f"'{new_db}'에 안전하게 남아있으니, 작업이 끝난 뒤 "
            f"'python etl/postgres_restore.py --retry-swap {new_db}'로 다시 "
            "시도하세요."
        )
    if reason == "race":
        return (
            f"교체 실패 - 그 사이 다른 세션이 이미 '{db}' 교체를 끝냈습니다. "
            f"복원된 데이터는 '{new_db}'에 안전하게 남아있으니, 필요하면 "
            "확인 후 직접 처리하세요."
        )
    return f"교체 실패 - 알 수 없는 사유({reason})입니다. 상태를 직접 확인하세요."


def find_idle_connections(conn, db: str) -> list[tuple[int, str | None]]:
    """db에 idle 상태로 연결된 세션의 (pid, application_name) 목록을 반환한다.

    자기 자신(conn)은 제외한다. 활성 트랜잭션이 있는 연결은 대상이 아니다
    - swap_databases()가 ObjectInUse로 알아서 안전하게 거부해주므로, 여기서는
    "진행 중인 작업이 없어 끊어도 안전한" idle 연결만 자동 정리 후보로 다룬다.
    """
    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT pid, application_name FROM pg_stat_activity "
            "WHERE datname = %s AND state = 'idle' AND pid <> pg_backend_pid()",
            (db,),
        )
        return cursor.fetchall()


def terminate_idle_connections(conn, pids: list[int]) -> None:
    """주어진 pid들을 pg_terminate_backend로 종료한다(호출자가 idle임을 확인한 뒤 호출)."""
    with conn.cursor() as cursor:
        for pid in pids:
            cursor.execute("SELECT pg_terminate_backend(%s)", (pid,))


def retry_swap(
    *,
    host: str,
    port: str,
    user: str,
    password: str,
    db: str,
    new_db: str,
    auto_yes: bool,
) -> None:
    """복원·검증까지 끝난 new_db를 db로 승격한다(교체 단계만 실행/재시도).

    정상 흐름의 4단계(교체)와 --retry-swap CLI 옵션(복원을 처음부터 다시
    하지 않고 교체만 재시도) 둘 다 이 함수 하나를 쓴다 - idle 연결 정리,
    이름 확인 프롬프트, 교체, 실패 메시지가 두 곳에 따로 있으면 나중에
    한쪽만 고쳐서 어긋나기 쉽기 때문이다. 실패하면 이 함수 안에서
    sys.exit()로 끝낸다(호출자가 반환값을 따로 처리할 필요 없음).
    """
    maintenance_conn = psycopg2.connect(
        host=host, port=port, dbname="postgres", user=user, password=password
    )
    maintenance_conn.autocommit = True

    if not target_database_exists(maintenance_conn, new_db):
        maintenance_conn.close()
        sys.exit(
            f"'{new_db}'가 존재하지 않습니다 - 이미 교체를 마쳤거나, 이름을 "
            "잘못 입력했을 수 있습니다."
        )

    live_db_exists = target_database_exists(maintenance_conn, db)
    previous_db_name = (
        build_previous_database_name(db, generate_restore_timestamp())
        if live_db_exists
        else None
    )
    if previous_db_name is not None:
        print(f"   기존 '{db}'는 지우지 않고 '{previous_db_name}'로 보존합니다.")
    else:
        print(f"   '{db}'가 아직 없어서 새로 만듭니다.")

    # RENAME은 이름이 바뀌는 양쪽 DB 모두에 활성 연결이 없어야 한다 - live_db
    # (db)뿐 아니라 new_db도 확인해야, 예를 들어 방금 복원에 썼던 연결이 아직
    # 안 닫혔거나 pgAdmin이 새 DB를 미리 열람해둔 경우도 자동으로 정리된다.
    for target in (db, new_db):
        idle_conns = find_idle_connections(maintenance_conn, target)
        if not idle_conns:
            continue
        apps = ", ".join(app or "(알 수 없음)" for _, app in idle_conns)
        print(f"   '{target}'에 idle 상태 연결 {len(idle_conns)}개 발견: {apps}")
        if auto_yes:
            print("   (--yes로 자동 종료)")
            terminate_idle_connections(maintenance_conn, [pid for pid, _ in idle_conns])
        else:
            answer = input(f"   '{target}'의 idle 연결을 종료하고 진행할까요? [y/N]: ")
            if answer.strip().lower() == "y":
                terminate_idle_connections(
                    maintenance_conn, [pid for pid, _ in idle_conns]
                )
            else:
                print(
                    "   idle 연결을 종료하지 않고 진행합니다(교체가 실패할 수 있습니다)."
                )

    if auto_yes:
        print("   (--yes로 확인 생략)")
    else:
        user_input = input(f"계속하려면 데이터베이스 이름을 그대로 입력하세요 [{db}]: ")
        if not restore_confirmed(user_input, db):
            maintenance_conn.close()
            sys.exit(
                "입력한 이름이 일치하지 않아 교체를 중단합니다. 복원된 데이터는 "
                f"'{new_db}'에 그대로 남아있습니다."
            )

    maintenance_conn.autocommit = False
    result = swap_databases(
        maintenance_conn, live_db=db, new_db=new_db, previous_db_name=previous_db_name
    )
    maintenance_conn.close()

    if result != "ok":
        sys.exit(build_swap_failure_message(result, db=db, new_db=new_db))

    print(f"   교체 완료. '{db}'가 '{new_db}'의 데이터를 가리킵니다.")
    if previous_db_name is not None:
        print(
            f"   기존 데이터는 '{previous_db_name}'로 보존됨 - 확인 후 필요 없으면 "
            "직접 삭제하세요."
        )


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
    parser.add_argument(
        "--retry-swap",
        metavar="NEW_DB_NAME",
        help=(
            "복원·검증까지는 끝났지만 교체(마지막 단계)만 실패한 경우, 처음부터 "
            "다시 복원하지 않고 이 단계만 재시도한다. 값은 이미 존재하는 새 DB "
            "이름(예: adventureworks_restore_20260821T090349Z)."
        ),
    )
    args = parser.parse_args()

    missing_vars = [name for name in REQUIRED_ENV_VARS if not os.environ.get(name)]
    if missing_vars:
        sys.exit(f".env에 다음 값이 없습니다: {', '.join(missing_vars)}")

    host = os.environ["POSTGRES_HOST"]
    port = os.environ["POSTGRES_PORT"]
    db = os.environ["POSTGRES_DB"]
    user = os.environ["POSTGRES_USER"]
    password = os.environ.get("POSTGRES_PASSWORD", "")
    print(f"대상: {host}:{port}/{db}")

    if args.retry_swap:
        print(f"교체 재시도 전용 모드: '{args.retry_swap}' -> '{db}'")
        retry_swap(
            host=host,
            port=port,
            user=user,
            password=password,
            db=db,
            new_db=args.retry_swap,
            auto_yes=args.yes,
        )
        return

    dump_path = Path(args.dump_path)
    if not dump_path.exists():
        sys.exit(f"덤프 파일이 없습니다: {dump_path}")

    print("0) 덤프 파일 확인 (사전 검증)")
    expected_tables = parse_created_tables(dump_path)
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
    print(f"1) 새 DB '{new_db}' 생성 (기존 '{db}'는 아직 건드리지 않음)")
    create_database(maintenance_conn, new_db)
    maintenance_conn.close()

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
    print(
        f"   복원 완료 (문장 {statement_count}개 실행, 테이블 {len(expected_tables)}개)"
    )

    print(f"3) 사후 검증 (새 DB '{new_db}' 대상, 테이블 존재 + 픽스처 값 대조)")
    from postgres_restore_validate import (
        build_fixture_checks,
        find_missing_tables,
        run_fixture_checks,
    )

    try:
        missing_tables = find_missing_tables(expected_tables, restore_conn)
        entities = load_fixture_entities()
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
    retry_swap(
        host=host,
        port=port,
        user=user,
        password=password,
        db=db,
        new_db=new_db,
        auto_yes=args.yes,
    )


if __name__ == "__main__":
    main()
