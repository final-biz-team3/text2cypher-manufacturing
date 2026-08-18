"""
시연 · 복구용: 특정 기간(YYYY-MM)의 트랜잭션 데이터를 삭제한다 (Bolt 드라이버 기반).

목적: 발표 시연 시나리오 재현, 또는 실제 데이터 정정 시 특정 기간을 지웠다가
0005 ADR "결정 3"의 강제 재적재 모드로 다시 채워 넣는 데 쓴다.

  1) 이 스크립트로 이미 적재된 데이터 중 특정 기간의 트랜잭션을 지운다
  2) UI에서 질의 -> 그 기간 데이터가 없어서 결과가 비거나 부족함을 보여준다
  3) python etl/export_to_csv.py tx --month <YYYY-MM> 실행
     python etl/load_to_neo4j.py --month <YYYY-MM> 실행
     (또는 python etl/run_monthly.py --month <YYYY-MM> 하나로 위 두 줄을 대신한다)
  4) 같은 질의를 다시 실행 -> 데이터가 채워진 것을 보여준다("업데이트 가능한 구조")

대상: WorkOrder/RoutingOperation, PurchaseOrder/PurchaseOrderLine, SalesOrder
      (해당 기간에 속하는 트랜잭션 노드만 DETACH DELETE. Product/Supplier 등
       마스터 노드와 마스터 관계는 건드리지 않는다)

실데이터를 지우는 작업이라 기본적으로 사람 확인(y/N)을 받고 진행한다
(--yes로 건너뛰기 가능).

사용법 (리포 루트 기준으로 실행):
    python etl/reset_month.py --month 2014-05
    python etl/reset_month.py --month 2014-05 --yes   # 확인 없이 바로 삭제(스크립트/CI용)
"""

import argparse
from pathlib import Path

from neo4j import GraphDatabase

ROOT_DIR = Path(__file__).resolve().parent.parent

PREVIEW_QUERY = """
MATCH (w:WorkOrder) WHERE toString(w.startDate) STARTS WITH $month
RETURN 'WorkOrder' AS label, count(w) AS n
UNION
MATCH (po:PurchaseOrder) WHERE toString(po.orderDate) STARTS WITH $month
RETURN 'PurchaseOrder' AS label, count(po) AS n
UNION
MATCH (so:SalesOrder) WHERE toString(so.orderDate) STARTS WITH $month
RETURN 'SalesOrder' AS label, count(so) AS n
"""

DELETE_WORKORDER = """
MATCH (w:WorkOrder)
WHERE toString(w.startDate) STARTS WITH $month
CALL (w) {
  OPTIONAL MATCH (w)-[:HAS_OPERATION]->(ro:RoutingOperation)
  FOREACH (x IN CASE WHEN ro IS NOT NULL THEN [ro] ELSE [] END | DETACH DELETE x)
  DETACH DELETE w
} IN TRANSACTIONS OF 500 ROWS
"""

DELETE_PURCHASEORDER = """
MATCH (po:PurchaseOrder)
WHERE toString(po.orderDate) STARTS WITH $month
CALL (po) {
  OPTIONAL MATCH (po)-[:HAS_LINE]->(pol:PurchaseOrderLine)
  FOREACH (x IN CASE WHEN pol IS NOT NULL THEN [pol] ELSE [] END | DETACH DELETE x)
  DETACH DELETE po
} IN TRANSACTIONS OF 500 ROWS
"""

DELETE_SALESORDER = """
MATCH (so:SalesOrder)
WHERE toString(so.orderDate) STARTS WITH $month
CALL (so) {
  DETACH DELETE so
} IN TRANSACTIONS OF 500 ROWS
"""


def load_env() -> dict:
    env_path = ROOT_DIR / ".env"
    env = {}
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        env[key.strip()] = value.strip()
    return env


def print_counts(session, month: str, title: str) -> dict:
    print(title)
    counts = {}
    for row in session.run(PREVIEW_QUERY, month=month):
        counts[row["label"]] = row["n"]
        print(f"  {row['label']}: {row['n']}")
    return counts


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--month", required=True, help="YYYY-MM")
    parser.add_argument("--yes", action="store_true", help="확인 프롬프트 없이 바로 삭제")
    args = parser.parse_args()

    env = load_env()
    uri, user, password = env["NEO4J_URI"], env["NEO4J_USER"], env["NEO4J_PASSWORD"]
    print(f"연결: {uri}")

    driver = GraphDatabase.driver(uri, auth=(user, password))
    try:
        driver.verify_connectivity()
        with driver.session() as session:
            before = print_counts(session, args.month, f"0. 삭제 대상 미리보기 ({args.month})")

            if sum(before.values()) == 0:
                print("삭제할 데이터가 없습니다.")
                return

            if not args.yes:
                answer = input("\n위 데이터를 삭제합니다. 계속할까요? [y/N] ").strip().lower()
                if answer != "y":
                    print("취소했습니다.")
                    return

            print("\n1. WorkOrder + RoutingOperation 삭제")
            session.run(DELETE_WORKORDER, month=args.month).consume()

            print("2. PurchaseOrder + PurchaseOrderLine 삭제")
            session.run(DELETE_PURCHASEORDER, month=args.month).consume()

            print("3. SalesOrder 삭제")
            session.run(DELETE_SALESORDER, month=args.month).consume()

            print()
            after = print_counts(session, args.month, f"4. 삭제 후 확인 ({args.month}, 전부 0이어야 함)")

            product_count = session.run("MATCH (p:Product) RETURN count(p) AS c").single()["c"]
            print(f"\n마스터 보존 확인: Product {product_count}건 (그대로여야 함)")

            if sum(after.values()) != 0:
                raise SystemExit(f"삭제 후에도 데이터가 남아있습니다: {after}")
    finally:
        driver.close()


if __name__ == "__main__":
    main()
