"""
반복해야 하는 "트랜잭션 export + 적재"를 한 번에 실행하는 오케스트레이터.

기본 동작은 워터마크 기반 실시간 증분(--since-last)이다. --month를 주면 특정
기간을 강제로 재적재한다(삭제 후 재적재 시연, 데이터 정정·재처리(backfill/
reprocessing)용 — 0005 ADR "결정 3" 참고).

동시 실행 방지를 위해 시작 시 etl/.lock 파일을 만들고 종료 시 지운다. 이미
락이 있으면(다른 실행이 진행 중이면) 즉시 종료한다. 비정상 종료로 락이 남아있는
경우엔 직접 지워야 한다 — 오래된 락을 자동으로 무시하는 판단은 하지 않는다
(진행 중인 다른 실행을 실수로 건드리는 것보다, 사람이 확인하고 지우는 쪽이 안전
하다고 봤다).

마스터 데이터는 여기서 다루지 않는다(1회 적재이므로 `python etl/export_to_csv.py
master`를 최초 1회만 수동으로 실행해두면 된다. `load_to_neo4j.py`는 매번
재실행돼도 마스터를 다시 MERGE(+prune)만 하므로 안전하다).

사용법 (리포 루트 기준으로 실행):
    python etl/run_monthly.py                       # 워터마크 증분(기본), as-of는 오늘
    python etl/run_monthly.py --as-of 2026-09-11     # as-of를 명시적으로 지정(리허설용)
    python etl/run_monthly.py --month 2014-06        # 특정 월 강제 재적재(백필·재처리)
"""

import argparse
import subprocess
import sys
from datetime import date
from pathlib import Path

ETL_DIR = Path(__file__).resolve().parent
ROOT_DIR = ETL_DIR.parent
LOCK_PATH = ETL_DIR / ".lock"


def run(cmd: list[str]) -> None:
    print(f"$ {' '.join(cmd)}")
    sys.stdout.flush()
    result = subprocess.run(cmd, cwd=ROOT_DIR)
    if result.returncode != 0:
        raise SystemExit(f"실패(exit {result.returncode}): {' '.join(cmd)}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--month",
        help="YYYY-MM. 지정하면 워터마크 대신 이 기간을 강제 재적재(backfill/reprocessing)한다",
    )
    parser.add_argument(
        "--as-of",
        help="YYYY-MM-DD (생략 시 오늘). 워터마크 증분 모드의 상한. 리허설용 오버라이드",
    )
    args = parser.parse_args()

    if LOCK_PATH.exists():
        raise SystemExit(
            f"이미 실행 중입니다({LOCK_PATH}가 존재). 다른 실행이 끝난 뒤 다시 시도하거나, "
            "비정상 종료로 남은 락이면 직접 지우고 재시도하세요."
        )
    LOCK_PATH.write_text(f"started_by={Path(sys.argv[0]).name}\n", encoding="utf-8")
    try:
        if args.month:
            print(f"=== {args.month} 강제 재적재(backfill/reprocessing) ===")
            run([sys.executable, str(ETL_DIR / "export_to_csv.py"), "tx", "--month", args.month])
            run([sys.executable, str(ETL_DIR / "load_to_neo4j.py"), "--month", args.month])
            print(f"=== {args.month} 완료 ===")
        else:
            as_of = args.as_of or date.today().strftime("%Y-%m-%d")
            print(f"=== 워터마크 증분 적재 (as-of {as_of}) ===")
            run([
                sys.executable, str(ETL_DIR / "export_to_csv.py"), "tx",
                "--since-last", "--as-of", as_of,
            ])
            run([sys.executable, str(ETL_DIR / "load_to_neo4j.py"), "--dir", "tx_incremental"])
            print("=== 완료 ===")
    finally:
        LOCK_PATH.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
