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
"""

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


def promote_neo4j_database(driver: Driver, db_name: str) -> str:
    """db_name을 새 기본 데이터베이스로 승격한다.

    기존 기본 데이터베이스는 먼저 멈춘다(STOP DATABASE) - Neo4j는 PostgreSQL의
    ALTER DATABASE RENAME 같은 실시간 개명 명령이 없어서, "이름을 살아있는
    그대로 바꿔치기"가 아니라 "기본값으로 어느 DB를 가리킬지"를 바꾸는 방식이다.
    이 사이 짧은 순간 기본 DB 접속이 끊길 수 있다(다운타임 감수, PR #16 리뷰
    논의에서 확정). 기존 DB는 지우지 않고 멈춘 채로 남는다.

    반환값: 승격 전 기본 데이터베이스였던 이름(사람이 롤백하고 싶을 때 참고).
    """
    with driver.session(database="system") as session:
        previous_default = session.run("SHOW DEFAULT DATABASE").single()["name"]
        session.run(f"STOP DATABASE `{previous_default}`")
        session.run("CALL dbms.setDefaultDatabase($name)", name=db_name)
    return previous_default


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
