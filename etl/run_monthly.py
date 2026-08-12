"""
매달 반복해야 하는 "트랜잭션 export + 적재"를 한 번에 실행하는 오케스트레이터.
cron/GitHub Actions/Windows 작업 스케줄러 등에서 이 스크립트 하나만 주기적으로
실행하도록 등록하면, docs/adr/0005-etl-batch-loading-pipeline.md "결과 및
트레이드오프"에 적힌 "매번 --month를 사람이 직접 지정해야 한다"는 문제가 해결된다.

이 스크립트는 실제 스케줄러 등록까지는 하지 않는다(그건 팀/환경마다 다르고, 시스템
설정을 바꾸는 일이라 별도로 결정해서 진행해야 한다) — 스케줄러가 호출할 대상만 준비한다.

마스터 데이터는 여기서 다루지 않는다(1회 적재이므로 `python etl/export_to_csv.py master`를
최초 1회만 수동으로 실행해두면 된다. `load_to_neo4j.py`/`load.cypher`는 매달 재실행돼도
마스터를 다시 MERGE만 하므로 안전하다).

사용법 (리포 루트 기준으로 실행):
    python etl/run_monthly.py                # 이번 달(현재 시스템 날짜 기준)
    python etl/run_monthly.py --month 2014-06 # 특정 월 강제 지정(백필용)
"""

import argparse
import subprocess
import sys
from datetime import date
from pathlib import Path

ETL_DIR = Path(__file__).resolve().parent
ROOT_DIR = ETL_DIR.parent


def run(cmd: list[str]) -> None:
    print(f"$ {' '.join(cmd)}")
    sys.stdout.flush()
    result = subprocess.run(cmd, cwd=ROOT_DIR)
    if result.returncode != 0:
        raise SystemExit(f"실패(exit {result.returncode}): {' '.join(cmd)}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--month", help="YYYY-MM (생략 시 현재 시스템 날짜의 월)")
    args = parser.parse_args()
    month = args.month or date.today().strftime("%Y-%m")

    print(f"=== {month} 트랜잭션 export + 적재 ===")
    run([sys.executable, str(ETL_DIR / "export_to_csv.py"), "tx", "--month", month])
    run([sys.executable, str(ETL_DIR / "run_load.py"), "--month", month])
    print(f"=== {month} 완료 ===")


if __name__ == "__main__":
    main()
