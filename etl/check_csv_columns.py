"""
`load_to_neo4j.py`의 각 Cypher 본문이 참조하는 `row.<컬럼명>`이 실제로 생성된
CSV 헤더에 다 있는지 자동으로 대조하는 스크립트. 배포/실행 전 CI 게이트로 쓴다.

`load_to_neo4j.py`의 MASTER_NODE_STEPS 등 STEP 목록(파일명 + Cypher 본문)을 그대로
재사용한다. 마스터 관계의 prune 단계(MASTER_REL_PRUNE_STEPS)는 `row.*`를 참조하지
않는(자연키 목록을 파라미터로 받는) 별도 쿼리라 이 대조 대상이 아니다.

사용법 (리포 루트 기준으로 실행):
    python etl/check_csv_columns.py                        # 마스터만 검사
    python etl/check_csv_columns.py --month 2014-05         # 마스터 + 강제 재적재 폴더
    python etl/check_csv_columns.py --dir tx_backfill       # 마스터 + 초기 백필 폴더
    python etl/check_csv_columns.py --dir tx_incremental    # 마스터 + 워터마크 증분 폴더
"""

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import load_to_neo4j as loader  # noqa: E402

ROW_REF = re.compile(r"row\.(\w+)")


def check_step(label: str, path: Path, body: str) -> bool:
    if not path.exists():
        print(f"  [스킵] {label}: {path} 없음")
        return True
    with path.open(encoding="utf-8", newline="") as f:
        header = f.readline().strip().split(",")
    header_set = set(header)
    refs = set(ROW_REF.findall(body))
    missing = refs - header_set
    if missing:
        print(f"  [불일치] {label}: CSV 헤더에 없는 컬럼 참조 -> {sorted(missing)}")
        return False
    print(f"  [OK] {label}: row.* {len(refs)}개 전부 CSV 헤더에 있음")
    return True


def main() -> None:
    parser = argparse.ArgumentParser()
    tx_group = parser.add_mutually_exclusive_group()
    tx_group.add_argument("--month", help="YYYY-MM. tx_<month>/ 도 같이 검사")
    tx_group.add_argument(
        "--dir",
        help="etl/import/ 아래 트랜잭션 폴더명(예: tx_backfill, tx_incremental)도 같이 검사",
    )
    args = parser.parse_args()

    ok = True
    master_dir = loader.IMPORT_DIR / "master"
    print("=== 마스터 ===")
    for label, filename, body in loader.MASTER_NODE_STEPS + loader.MASTER_REL_STEPS:
        ok = check_step(label, master_dir / filename, body) and ok

    tx_dir_name = f"tx_{args.month}" if args.month else args.dir
    if tx_dir_name:
        tx_dir = loader.IMPORT_DIR / tx_dir_name
        print(f"=== 트랜잭션 ({tx_dir_name}) ===")
        for label, filename, body in loader.TX_NODE_STEPS + loader.TX_REL_STEPS:
            ok = check_step(label, tx_dir / filename, body) and ok

    print("\n전체 결과:", "OK" if ok else "불일치 발견")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
