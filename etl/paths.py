"""etl/ 스크립트가 공유하는 경로 상수.

여러 모듈이 각자 Path(__file__).resolve().parent.parent를 정의하고 있었고,
그중 Neo4j 동기화·검증 쪽은 이 상수 하나 때문에 PostgreSQL 복원 모듈
(postgres_restore)을 import하는 방향이 뒤집힌 의존을 만들고 있었다. 어느
모듈에도 의존하지 않는 이 파일에 한 번만 둔다.
"""

from pathlib import Path

# 리포지토리 루트(etl/의 부모). schema/, queries/, .env가 여기 있다.
ROOT_DIR = Path(__file__).resolve().parent.parent
