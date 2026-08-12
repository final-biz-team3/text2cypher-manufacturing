"""
`load_to_neo4j.py`의 각 Cypher 본문이 참조하는 `row.<컬럼명>`이 실제로 생성된
CSV 헤더에 다 있는지 자동으로 대조하는 스크립트.

docs/adr/0005-etl-batch-loading-pipeline.md "실행 검증(export)"에 "`load.cypher`의
`LOAD CSV` 24개 블록이 참조하는 `row.*` 컬럼명을 생성된 CSV 헤더와 전부 대조해
불일치 없음을 확인했다"고 적혀 있는데, 그건 1회성으로 손으로 확인한 것이었다.
이 스크립트는 그 대조를 재현 가능하게 만든 것이다.

`load_to_neo4j.py`의 MASTER_NODE_STEPS 등 STEP 목록(파일명 + Cypher 본문)을 그대로
재사용한다 — `load.cypher`를 별도로 정규식 파싱하지 않는 이유는, 주석·문법이 자유로운
`.cypher` 파일을 안정적으로 파싱하기보다 이미 구조화돼 있는 `load_to_neo4j.py`의
데이터를 재사용하는 쪽이 더 신뢰할 수 있기 때문이다. 두 파일(`load.cypher`와
`load_to_neo4j.py`)은 같은 로직을 각자 유지하므로, 컬럼 참조가 서로 어긋나지 않게
관리하는 책임은 여전히 사람에게 있다.

사용법 (리포 루트 기준으로 실행):
    python etl/check_csv_columns.py                 # 마스터만 검사
    python etl/check_csv_columns.py --month 2014-05  # 마스터 + 해당 월 트랜잭션도 검사
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
    parser.add_argument("--month", help="YYYY-MM. 지정하면 tx_<month>/ 도 같이 검사")
    args = parser.parse_args()

    ok = True
    master_dir = loader.IMPORT_DIR / "master"
    print("=== 마스터 ===")
    for label, filename, body in loader.MASTER_NODE_STEPS + loader.MASTER_REL_STEPS:
        ok = check_step(label, master_dir / filename, body) and ok

    if args.month:
        tx_dir = loader.IMPORT_DIR / f"tx_{args.month}"
        print(f"=== 트랜잭션 ({args.month}) ===")
        for label, filename, body in loader.TX_NODE_STEPS + loader.TX_REL_STEPS:
            ok = check_step(label, tx_dir / filename, body) and ok

    print("\n전체 결과:", "OK" if ok else "불일치 발견")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
