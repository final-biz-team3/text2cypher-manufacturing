"""etl/ 스크립트가 공유하는 리포지토리 경로와, 그 경로에서 읽는 공용 리소스.

여러 모듈이 각자 Path(__file__).resolve().parent.parent를 정의하고 있었고,
그중 Neo4j 동기화·검증 쪽은 이 상수 하나 때문에 PostgreSQL 복원 모듈
(postgres_restore)을 import하는 방향이 뒤집힌 의존을 만들고 있었다. 어느
모듈에도 의존하지 않는 이 파일에 한 번만 둔다.
"""

import json
from pathlib import Path
from typing import Any

# 리포지토리 루트(etl/의 부모). schema/, queries/, .env가 여기 있다.
ROOT_DIR = Path(__file__).resolve().parent.parent


def load_fixture_entities() -> dict[str, Any]:
    """queries/query_parameters.json의 entities 블록을 읽는다.

    구조화 MVP 검증(Neo4j)과 복원 검증(PostgreSQL) 양쪽이 같은 정답 fixture
    파일을 쓰는데, 경로 리터럴 + json 파싱 보일러플레이트가 네 곳에 복제돼
    있어 한 곳으로 모은다.
    """
    path = ROOT_DIR / "queries" / "query_parameters.json"
    return json.loads(path.read_text(encoding="utf-8"))["entities"]
