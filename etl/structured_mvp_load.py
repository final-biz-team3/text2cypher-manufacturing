"""구조화 MVP 노드·관계를 배치(UNWIND)로 Neo4j에 MERGE하고, 제약조건 DDL을
적용하고, 이번 syncRunId에 없는 stale 데이터를 prune한다.

structured_mvp_loading_rules.md 6절의 prune Cypher를 그대로 사용한다 - 관계를
먼저 지우고, 그 다음 업무 노드 6개 라벨만 정확히 지운다(온톨로지 노드는
아직 없으므로 해당 없음).
"""

from collections.abc import Iterator
from pathlib import Path
from typing import Any

from neo4j import Driver
from structured_mvp_spec import NodeSpec, RelationshipSpec

BATCH_SIZE = 1000

PRUNE_RELATIONSHIPS_CYPHER = """
MATCH ()-[r:SUPPLIES|REQUIRES_COMPONENT|PRODUCES|HAS_OPERATION|PERFORMED_AT|SCRAPPED_DUE_TO]->()
WHERE r.syncRunId <> $syncRunId OR r.syncRunId IS NULL
DELETE r
"""

PRUNE_NODES_CYPHER = """
MATCH (n)
WHERE any(label IN labels(n) WHERE label IN $businessLabels)
  AND (n.syncRunId <> $syncRunId OR n.syncRunId IS NULL)
DETACH DELETE n
"""

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


def apply_constraints(driver: Driver, cypher_path: Path) -> None:
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
    with driver.session() as session:
        for statement in statements:
            session.run(statement)


def load_rows(
    driver: Driver,
    spec: NodeSpec | RelationshipSpec,
    rows: list[dict[str, Any]],
    sync_run_id: str,
) -> None:
    """UNWIND $rows AS row + spec.merge_cypher를 배치 단위로 실행한다."""
    cypher = f"UNWIND $rows AS row\n{spec.merge_cypher}"
    with driver.session() as session:
        for batch in chunk_rows(rows):
            session.run(cypher, rows=batch, syncRunId=sync_run_id)


def prune_stale(driver: Driver, sync_run_id: str) -> None:
    """이번 syncRunId가 아닌 업무 관계·노드를 정리한다. 관계 먼저, 노드 나중."""
    with driver.session() as session:
        session.run(PRUNE_RELATIONSHIPS_CYPHER, syncRunId=sync_run_id)
        session.run(
            PRUNE_NODES_CYPHER, syncRunId=sync_run_id, businessLabels=BUSINESS_LABELS
        )
