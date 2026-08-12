"""
`demo_reset_month.cypher`의 Bolt 드라이버 버전 (경로 B — 파일시스템 접근 없이 원격
Neo4j에 붙는 상황용). 발표 시연 시나리오(한 달치 트랜잭션을 지웠다가, 월별 적재
스크립트로 다시 채워지는 걸 보여주는 것)를 원격 공유 서버에서도 실행할 수 있게
`demo_reset_month.cypher`와 동일한 로직·순서를 그대로 옮겼다:

  0. 삭제 대상 미리보기
  1. WorkOrder + RoutingOperation 삭제
  2. PurchaseOrder + PurchaseOrderLine 삭제
  3. SalesOrder 삭제 (CONTAINS_PRODUCT는 관계라 DETACH DELETE 시 함께 제거됨)
  4. 삭제 후 확인 (전부 0이어야 함) + 마스터 보존 확인(Product 카운트 그대로인지)

`demo_reset_month.cypher`와의 차이점: cypher-shell로 파일 전체를 실행하면 미리보기
직후 바로 삭제까지 진행되지만, 이 스크립트는 원격 공유 서버의 실데이터를 지우는
작업이라 기본적으로 `--yes` 없이는 사람 확인(y/N)을 받고 진행한다.

사용법 (리포 루트 기준으로 실행):
    python etl/reset_month.py --month 2014-05
    python etl/reset_month.py --month 2014-05 --yes   # 확인 없이 바로 삭제(스크립트/CI용)

삭제 후 다시 채우려면:
    python etl/run_monthly.py --month 2014-05
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
CALL { WITH w
  OPTIONAL MATCH (w)-[:HAS_OPERATION]->(ro:RoutingOperation)
  FOREACH (x IN CASE WHEN ro IS NOT NULL THEN [ro] ELSE [] END | DETACH DELETE x)
  DETACH DELETE w
} IN TRANSACTIONS OF 500 ROWS
"""

DELETE_PURCHASEORDER = """
MATCH (po:PurchaseOrder)
WHERE toString(po.orderDate) STARTS WITH $month
CALL { WITH po
  OPTIONAL MATCH (po)-[:HAS_LINE]->(pol:PurchaseOrderLine)
  FOREACH (x IN CASE WHEN pol IS NOT NULL THEN [pol] ELSE [] END | DETACH DELETE x)
  DETACH DELETE po
} IN TRANSACTIONS OF 500 ROWS
"""

DELETE_SALESORDER = """
MATCH (so:SalesOrder)
WHERE toString(so.orderDate) STARTS WITH $month
CALL { WITH so
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
