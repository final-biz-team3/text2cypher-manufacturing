"""구조화 MVP 동기화·검증 두 진입점이 공유하는 Neo4j 접속 설정.

run_structured_mvp_sync.py와 structured_mvp_validate.py가 서로를 import해서
생기던 순환(검증 쪽이 이를 피하려고 함수 지역 import를 쓰던 냄새)을 없애려고,
두 곳이 공통으로 쓰는 env 목록·관계 타입 목록·접속 헬퍼를 어느 쪽도 의존하지
않는 이 모듈로 내린다.
"""

import os
import sys

from neo4j import Driver, GraphDatabase
from structured_mvp_spec import NODE_SPECS, RELATIONSHIP_SPECS

NEO4J_REQUIRED_ENV_VARS = ["NEO4J_URI", "NEO4J_USER", "NEO4J_PASSWORD"]

# 스펙 테이블에서 파생 - 노드/관계가 추가되면 structured_mvp_spec.py만 고치면 된다.
BUSINESS_LABELS = [spec.label for spec in NODE_SPECS]
RELATIONSHIP_TYPES = [spec.rel_type for spec in RELATIONSHIP_SPECS]


def connect_neo4j_from_env() -> Driver:
    """env 확인 → 드라이버 생성 → verify_connectivity. 실패 시 sys.exit.

    sync/validate 두 진입점이 같은 절차를 각자 복제하고 있어, env 누락·접속
    실패 메시지가 갈라지지 않게 한 곳에 모은다.
    """
    missing_vars = [
        name for name in NEO4J_REQUIRED_ENV_VARS if not os.environ.get(name)
    ]
    if missing_vars:
        sys.exit(f".env에 다음 값이 없습니다: {', '.join(missing_vars)}")

    driver = GraphDatabase.driver(
        os.environ["NEO4J_URI"],
        auth=(os.environ["NEO4J_USER"], os.environ["NEO4J_PASSWORD"]),
    )
    try:
        driver.verify_connectivity()
    except Exception as exc:
        sys.exit(f"Neo4j 접속 실패 ({os.environ['NEO4J_URI']}): {exc}")
    return driver
