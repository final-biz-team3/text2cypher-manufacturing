"""구조화 MVP 노드·관계를 배치(UNWIND)로 Neo4j에 MERGE하고, 제약조건 DDL을
적용하고, 새 DB를 만들어 기본 데이터베이스로 승격한다.

PR #16 리뷰(josephuk77 3차) 대응으로 prune 기반 적재를 버렸다 - 매 실행마다
이번 syncRunId가 아닌 데이터를 지우는 방식은, 검증을 통과하기 전에 이미 일부
배치가 라이브 그래프에 커밋돼버려서 쓰기 도중 실패 시 라이브 그래프가 부분
갱신 상태로 남는 문제가 있었다(리뷰에서 지적, run_structured_mvp_sync.py 참고).
대신 매 실행마다 새 Neo4j 데이터베이스를 만들어 거기에만 적재·검증하고,
통과했을 때만 기본 데이터베이스로 승격한다 - 실패해도 라이브 데이터베이스는
한 번도 안 건드려서 부분 갱신이 원천적으로 불가능하다. 기존 기본 데이터베이스는
지우지 않고 멈춘 채로 남는다(사람이 확인 후 직접 정리, PostgreSQL의
{db}_previous_<timestamp> 보존과 같은 철학).

승격(retry_promote) 안전장치: Neo4j는 PostgreSQL의 ALTER DATABASE RENAME
같은 원자적 개명 명령이 없어서(STOP DATABASE + setDefaultDatabase로 흉내냄),
PostgreSQL과 똑같은 수준의 안전을 앱 레벨에서 직접 만들어야 한다. 실제로
겪은 문제(2026-08-21, 라이브 테스트로 확인): STOP DATABASE는 활성 트랜잭션이
있어도 안전장치 없이 강제로 끊어버린다(PostgreSQL의 ObjectInUse 같은 보호가
없음). 그래서 STOP DATABASE 호출 전에 SHOW TRANSACTIONS로 대상 DB의 활성
트랜잭션을 직접 확인해서 있으면 거부한다. 또한 setDefaultDatabase()에는
원자적 충돌 감지가 없어서, 두 세션이 동시에 승격을 시도하면 나중 세션이
먼저 세션이 이미 끝낸 승격을 조용히 덮어쓸 수 있다(PostgreSQL에는 없는,
Neo4j만의 추가 위험) - 그래서 호출자가 작업 시작 시점에 확인해둔 "그때의
기본 데이터베이스" 이름을 넘기면, 승격 직전에 실제 기본 데이터베이스가 그
사이 바뀌지 않았는지 재확인한다(낙관적 동시성 제어). 이 두 확인과 승격
자체, 실패 메시지를 retry_promote() 함수 하나로 묶어서 정상 흐름
(run_structured_mvp_sync.py)과 재시도 CLI(--retry-promote) 둘 다 어긋나지
않게 한다 - PostgreSQL postgres_restore.retry_swap()과 같은 설계다.
"""

import sys
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from neo4j import Driver
from structured_mvp_spec import NodeSpec, RelationshipSpec

BATCH_SIZE = 1000

BUSINESS_LABELS = [
    "Product",
    "Supplier",
    "WorkOrder",
    "RoutingOperation",
    "Location",
    "ScrapReason",
]


def chunk_rows(rows: list[Any], batch_size: int = BATCH_SIZE) -> Iterator[list[Any]]:
    """행 목록을 batch_size 단위로 잘라 순서대로 내놓는다."""
    for start in range(0, len(rows), batch_size):
        yield rows[start : start + batch_size]


def generate_database_name(prefix: str = "mvpgraph") -> str:
    """새 Neo4j 데이터베이스 이름을 만든다.

    Neo4j 데이터베이스 이름 규칙(영문으로 시작, 이후 영숫자·점·대시만 허용,
    밑줄 금지, 3~63자, 소문자로 정규화)에 맞춰 대시로 구분한다.
    """
    timestamp = datetime.now(UTC).strftime("%Y%m%dt%H%M%Sz")
    return f"{prefix}-{timestamp}"


def create_neo4j_database(driver: Driver, db_name: str) -> None:
    """system 데이터베이스에 대고 새 데이터베이스를 만든다.

    db_name은 항상 generate_database_name()이 만든 우리 통제하의 문자열이라
    (외부 입력 없음) 백틱 리터럴 삽입이 안전하다 - Cypher 관리 명령은 DB
    이름에 쿼리 파라미터를 못 쓴다.
    """
    with driver.session(database="system") as session:
        session.run(f"CREATE DATABASE `{db_name}` WAIT")


def get_default_database(driver: Driver) -> str:
    """현재 기본 데이터베이스 이름을 확인한다."""
    with driver.session(database="system") as session:
        result = session.run("SHOW DEFAULT DATABASE").single()
        assert result is not None
        return str(result["name"])


def find_active_transactions(driver: Driver, db_name: str) -> list[dict[str, Any]]:
    """db_name에서 실행 중인 트랜잭션 목록을 반환한다(비어 있으면 없음).

    STOP DATABASE 호출 전에 반드시 확인한다 - Neo4j의 STOP DATABASE는
    PostgreSQL의 ALTER DATABASE RENAME과 달리 활성 트랜잭션이 있어도
    안전장치 없이 강제로 끊어버린다(라이브 테스트로 실제 확인, 2026-08-21).
    """
    with driver.session(database="system") as session:
        result = session.run(
            "SHOW TRANSACTIONS YIELD database, transactionId, currentQuery "
            "WHERE database = $db "
            "RETURN transactionId, currentQuery",
            db=db_name,
        )
        return [dict(record) for record in result]


def build_promotion_failure_message(
    reason: str,
    *,
    previous_default: str,
    new_db: str,
    actual_default: str | None = None,
) -> str:
    """retry_promote()의 실패 사유(reason)에 맞는 안내 메시지를 만든다."""
    if reason == "transactions_in_use":
        return (
            f"승격 실패 - '{previous_default}'에서 현재 트랜잭션을 이용 중입니다. "
            f"데이터 적재 or 교체가 불가합니다. 새로 적재된 데이터는 '{new_db}'에 "
            "안전하게 남아있으니, 트랜잭션이 끝난 뒤 나중에 다시 시도하세요."
        )
    if reason == "race":
        return (
            f"승격 실패 - 기본 데이터베이스가 그 사이에 다른 세션에 의해 이미 "
            f"바뀌었습니다('{previous_default}' -> '{actual_default}'). 새로 "
            f"적재된 데이터는 '{new_db}'에 안전하게 남아있으니, 필요하면 확인 후 "
            "나중에 다시 시도하세요."
        )
    return f"승격 실패 - 알 수 없는 사유({reason})입니다. 상태를 직접 확인하세요."


def retry_promote(
    driver: Driver,
    new_db: str,
    *,
    expected_previous_default: str | None = None,
) -> None:
    """new_db를 기본 데이터베이스로 승격한다(승격 단계만 실행/재시도).

    run_structured_mvp_sync.py 정상 흐름의 마지막 단계와 --retry-promote CLI
    옵션(적재·검증은 끝났지만 승격만 실패한 경우, 처음부터 다시 적재하지 않고
    이 단계만 재시도) 둘 다 이 함수 하나를 쓴다 - 트랜잭션 확인, 동시 승격
    경합 확인, 승격 실행, 실패 메시지가 두 곳에 따로 있으면 나중에 한쪽만
    고쳐서 어긋나기 쉽기 때문이다(PostgreSQL postgres_restore.retry_swap()과
    같은 설계). 실패하면 이 함수 안에서 sys.exit()로 끝낸다.

    expected_previous_default: 호출자가 작업을 시작할 때(추출·적재를 시작하기
    전) 미리 확인해둔 "그때의 기본 데이터베이스" 이름. 넘기면, 승격 직전에
    실제 기본 데이터베이스가 그 사이(오래 걸리는 추출·적재 도중) 바뀌지
    않았는지 재확인한다 - 바뀌었다면 다른 세션이 먼저 승격을 끝낸 것이므로
    조용히 덮어쓰지 않고 안전하게 거부한다. 넘기지 않으면(예: "작업 시작
    시점"이 따로 없는 단독 --retry-promote 실행) 이 확인은 건너뛴다.
    """
    actual_default = get_default_database(driver)

    if (
        expected_previous_default is not None
        and actual_default != expected_previous_default
    ):
        sys.exit(
            build_promotion_failure_message(
                "race",
                previous_default=expected_previous_default,
                new_db=new_db,
                actual_default=actual_default,
            )
        )

    if find_active_transactions(driver, actual_default):
        sys.exit(
            build_promotion_failure_message(
                "transactions_in_use", previous_default=actual_default, new_db=new_db
            )
        )

    with driver.session(database="system") as session:
        session.run(f"STOP DATABASE `{actual_default}`")
        session.run("CALL dbms.setDefaultDatabase($name)", name=new_db)

    print(f"   승격 완료. '{new_db}'가 이제 기본 데이터베이스입니다.")
    print(
        f"   기존 기본 데이터베이스 '{actual_default}'는 멈춘 채로 보존됨 - "
        "확인 후 필요 없으면 직접 삭제하세요."
    )


def apply_constraints(driver: Driver, cypher_path: Path, *, database: str) -> None:
    """schema/structured_mvp_constraints.cypher의 각 문장을 순서대로 실행한다.

    먼저 `//` 주석 줄을 제거한 뒤 `;`로 나눈다 - 파일 맨 앞 주석 블록 뒤에
    개행만 있고 세미콜론이 없어서, 주석 제거 없이 바로 `;`로 나누면 첫 번째
    조각(주석 + 첫 CREATE CONSTRAINT 문)이 "//로 시작한다"는 이유로 통째로
    걸러져 제약조건이 조용히 누락되는 버그가 있었다.
    """
    non_comment_lines = [
        line
        for line in cypher_path.read_text(encoding="utf-8").splitlines()
        if not line.strip().startswith("//")
    ]
    statements = [
        s.strip() for s in "\n".join(non_comment_lines).split(";") if s.strip()
    ]
    with driver.session(database=database) as session:
        for statement in statements:
            session.run(statement)


def load_rows(
    driver: Driver,
    spec: NodeSpec | RelationshipSpec,
    rows: list[dict[str, Any]],
    sync_run_id: str,
    *,
    database: str,
) -> None:
    """UNWIND $rows AS row + spec.merge_cypher를 배치 단위로 실행한다."""
    cypher = f"UNWIND $rows AS row\n{spec.merge_cypher}"
    with driver.session(database=database) as session:
        for batch in chunk_rows(rows):
            session.run(cypher, rows=batch, syncRunId=sync_run_id)
