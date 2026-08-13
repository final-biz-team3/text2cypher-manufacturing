"""
AdventureWorks(자전거 공정 데이터) xlsx -> Neo4j Bolt 적재용 노드/관계 CSV 생성 스크립트

기준 문서: docs/adr/0004-graph-schema-v2.md (스키마 설계) / docs/adr/0005-etl-batch-loading-pipeline.md (마스터/트랜잭션 분리, 배치·워터마크 적재)

마스터(시간에 안 묶이는) 데이터는 1회만 적재하고, 트랜잭션(배치에 묶이는) 데이터는
세 가지 모드로 나눠 export한다.

사용법 (리포 루트 기준으로 실행):
    python etl/export_to_csv.py master
        -> etl/import/master/ 아래에 마스터 노드 6종 + 관계 5종 CSV 생성 (1회 실행)

    python etl/export_to_csv.py tx --before 2026-09-11
        -> etl/import/tx_backfill/ 아래에 2026-09-11 이전 전체 이력 CSV 생성 (초기 백필)

    python etl/export_to_csv.py tx --since-last [--as-of 2026-09-11]
        -> etl/import/tx_incremental/ 아래에 (Neo4j에서 조회한 워터마크, as-of] 구간 CSV 생성
           (--as-of 생략 시 오늘 날짜. 실시간 증분의 기본 실행 형태)

    python etl/export_to_csv.py tx --month 2014-05
        -> etl/import/tx_2014-05/ 아래에 그 달에 속한 트랜잭션 CSV 생성
           (강제 재적재 — 삭제 후 재적재 시연, 데이터 정정·재처리(backfill/reprocessing)용)

세 모드 모두 월 기준 컬럼(PurchaseOrder.orderDate, SalesOrder.orderDate, WorkOrder.startDate)
으로 대상 행을 고른다. PurchaseOrderLine/RoutingOperation은 각자 부모의 마스크를 그대로
물려받아 동반 적재된다(부모와 자식이 서로 다른 배치로 갈라지는 것을 방지하기 위함).

입력 파일 위치: etl/data/AdventureWorks_전체사슬_32시트_한글.xlsx
    이 파일은 용량이 커서(약 32MB) git에 커밋하지 않는다(.gitignore 처리됨).
    팀 공유 드라이브 등에서 받아 이 경로에 직접 두면 된다.

출력 위치: etl/import/ (Bolt 드라이버(load_to_neo4j.py)가 로컬에서 직접 읽어 전송하므로
    Neo4j 컨테이너에 마운트할 필요가 없다)

날짜 변환은 그래프 스키마 v2에 명시된 속성 타입을 그대로 따른다.
  - DATE 타입 속성(sellStartDate, orderDate, startDate 등) -> 'YYYY-MM-DD' (Cypher date() 파싱용)
  - LOCAL DATETIME 타입 속성(모든 modifiedAt) -> 'YYYY-MM-DDTHH:MM:SS' (Cypher localdatetime() 파싱용)
불리언 컬럼('예'/'아니오')은 'true'/'false' 문자열로 변환한다.

팀 결정 반영:
  1) 폐기 사유 표시는 조회 시점 OPTIONAL MATCH로 처리 -> export에서는 특별 처리 없음
  2) 현재 데이터에 폐기 사유 누락 없음 확인됨 -> scrapReasonId NULL 행만 자연히 dropna로 제외
  3) BOM 유효기간 필터는 조회 시점에 적용 -> export에서는 startDate/endDate를 그대로 실어보냄
  4) 비활성 공급업체(활성여부='아니오')는 적재 전(export 단계)에 SUPPLIES에서 제외
"""

import argparse
from pathlib import Path

import pandas as pd

ETL_DIR = Path(__file__).resolve().parent
ROOT_DIR = ETL_DIR.parent
SRC = ETL_DIR / "data" / "AdventureWorks_전체사슬_32시트_한글.xlsx"
IMPORT_DIR = ETL_DIR / "import"

TRANSACTION_WATERMARK_COLUMNS = {
    "PurchaseOrder": "orderDate",
    "SalesOrder": "orderDate",
    "WorkOrder": "startDate",
}


def to_date(series):
    """DATE 타입 속성용: 'YYYY-MM-DD'"""
    s = pd.to_datetime(series, errors="coerce")
    return s.dt.strftime("%Y-%m-%d")


def to_iso(series):
    """LOCAL DATETIME 타입 속성(modifiedAt)용: 'YYYY-MM-DDTHH:MM:SS'"""
    s = pd.to_datetime(series, errors="coerce")
    return s.dt.strftime("%Y-%m-%dT%H:%M:%S")


def to_bool(series):
    return series.map({"예": "true", "아니오": "false"}).fillna("")


def read(sheet, **kw):
    if not SRC.exists():
        raise FileNotFoundError(
            f"원본 데이터 파일을 찾을 수 없습니다: {SRC}\n"
            "팀 공유 드라이브에서 AdventureWorks_전체사슬_32시트_한글.xlsx 를 받아 "
            "etl/data/ 아래에 두세요."
        )
    return pd.read_excel(SRC, sheet_name=sheet, dtype=str, **kw)


def save(df, out_dir: Path, name):
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / name
    df.to_csv(path, index=False)
    print(f"{name}: {len(df)} rows -> {path}")


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


def get_watermarks() -> dict[str, str | None]:
    """Bolt로 라벨별 MAX(날짜 컬럼)을 조회한다. 그래프에 아직 해당 라벨이 없으면 None."""
    from neo4j import GraphDatabase

    env = load_env()
    driver = GraphDatabase.driver(env["NEO4J_URI"], auth=(env["NEO4J_USER"], env["NEO4J_PASSWORD"]))
    watermarks: dict[str, str | None] = {}
    try:
        with driver.session() as session:
            for label, column in TRANSACTION_WATERMARK_COLUMNS.items():
                value = session.run(f"MATCH (n:{label}) RETURN max(n.{column}) AS wm").single()["wm"]
                watermarks[label] = str(value) if value is not None else None
    finally:
        driver.close()
    return watermarks


# =============================================================================
# 마스터 데이터 (1회 적재) — Product, Supplier, ProductCategory, ProductSubcategory,
#                            Location, ScrapReason 노드 6종 +
#                            SUPPLIES, REQUIRES_COMPONENT, STOCKED_AT,
#                            IN_SUBCATEGORY, IN_CATEGORY 관계 5종
# =============================================================================

def export_master(out_dir: Path = IMPORT_DIR / "master"):
    df_product = read("제품·부품마스터")
    df_supplier = read("공급업체")
    df_category = read("제품대분류")
    df_subcategory = read("제품중분류")
    df_location = read("작업장")
    df_scrap = read("폐기사유")
    df_bom = read("자재명세서BOM")
    df_inventory = read("재고")
    df_productvendor = read("부품-공급업체")

    # ---------------- 노드 ----------------
    save(pd.DataFrame({
        "productId": df_product["제품ID"],
        "name": df_product["명칭"],
        "productNumber": df_product["제품번호"],
        "makeInHouse": to_bool(df_product["자체제조여부"]),
        "sellableFinishedGood": to_bool(df_product["판매완제품여부"]),
        "color": df_product["색상"],
        "safetyStockLevel": df_product["안전재고수준"],
        "reorderPoint": df_product["재주문점"],
        "standardCost": df_product["표준원가"],
        "listPrice": df_product["정가"],
        "size": df_product["규격"],
        "sizeUnit": df_product["규격단위"].str.strip(),
        "weightUnit": df_product["중량단위"].str.strip(),
        "weight": df_product["중량"],
        "daysToManufacture": df_product["제조소요일수"],
        "productLine": df_product["제품라인"],
        "classCode": df_product["등급"],
        "styleCode": df_product["스타일"],
        "sellStartDate": to_date(df_product["판매시작일"]),
        "sellEndDate": to_date(df_product["판매종료일"]),
        "discontinuedDate": to_date(df_product["단종일"]),
        "rowGuid": df_product["행GUID"],
        "modifiedAt": to_iso(df_product["수정일시"]),
    }), out_dir, "nodes_product.csv")

    save(pd.DataFrame({
        "supplierId": df_supplier["주체ID"],
        "accountNumber": df_supplier["계정번호"],
        "name": df_supplier["명칭"],
        "creditRating": df_supplier["신용등급"],
        "preferred": to_bool(df_supplier["우선공급업체여부"]),
        "active": to_bool(df_supplier["활성여부"]),
        "purchasingWebUrl": df_supplier["구매웹서비스URL"],
        "modifiedAt": to_iso(df_supplier["수정일시"]),
    }), out_dir, "nodes_supplier.csv")

    save(pd.DataFrame({
        "categoryId": df_category["대분류ID"],
        "name": df_category["명칭"],
        "nameKo": df_category["대분류_한글"],
        "rowGuid": df_category["행GUID"],
        "modifiedAt": to_iso(df_category["수정일시"]),
    }), out_dir, "nodes_product_category.csv")

    save(pd.DataFrame({
        "subcategoryId": df_subcategory["중분류ID"],
        "name": df_subcategory["명칭"],
        "nameKo": df_subcategory["중분류_한글"],
        "rowGuid": df_subcategory["행GUID"],
        "modifiedAt": to_iso(df_subcategory["수정일시"]),
    }), out_dir, "nodes_product_subcategory.csv")

    save(pd.DataFrame({
        "locationId": df_location["작업장ID"],
        "name": df_location["명칭"],
        "nameKo": df_location["작업장명_한글"],
        "costRate": df_location["시간당비용"],
        "availability": df_location["가용시간"],
        "modifiedAt": to_iso(df_location["수정일시"]),
    }), out_dir, "nodes_location.csv")

    save(pd.DataFrame({
        "scrapReasonId": df_scrap["폐기사유ID"],
        "name": df_scrap["명칭"],
        "nameKo": df_scrap["폐기사유_한글"],
        "modifiedAt": to_iso(df_scrap["수정일시"]),
    }), out_dir, "nodes_scrap_reason.csv")

    # ---------------- 관계 ----------------
    save(pd.DataFrame({
        "productId": df_product["제품ID"],
        "subcategoryId": df_product["중분류ID"],
    }).dropna(), out_dir, "rels_in_subcategory.csv")

    save(pd.DataFrame({
        "subcategoryId": df_subcategory["중분류ID"],
        "categoryId": df_subcategory["대분류ID"],
    }).dropna(), out_dir, "rels_in_category.csv")

    bom = pd.DataFrame({
        "bomId": df_bom["BOM_ID"],
        "assemblyProductId": df_bom["상위조립품ID"],
        "componentProductId": df_bom["하위부품ID"],
        "startDate": to_date(df_bom["시작일"]),
        "endDate": to_date(df_bom["종료일"]),
        "unitCode": df_bom["단위"].str.strip(),
        "bomLevel": df_bom["BOM레벨"],
        "quantityPerAssembly": df_bom["상위1개당소요수량"],
        "modifiedAt": to_iso(df_bom["수정일시"]),
    }).dropna(subset=["assemblyProductId"])
    save(bom, out_dir, "rels_requires_component.csv")

    save(pd.DataFrame({
        "inventoryGuid": df_inventory["행GUID"],
        "productId": df_inventory["제품ID"],
        "locationId": df_inventory["작업장ID"],
        "shelf": df_inventory["선반"],
        "bin": df_inventory["구역"],
        "quantity": df_inventory["수량"],
        "modifiedAt": to_iso(df_inventory["수정일시"]),
    }), out_dir, "rels_stocked_at.csv")

    # 팀 결정 4: 비활성 공급업체(활성여부='아니오')는 적재 전 제외
    active_ids = set(df_supplier.loc[df_supplier["활성여부"] == "예", "주체ID"])
    supplies = pd.DataFrame({
        "supplierId": df_productvendor["주체ID"],
        "productId": df_productvendor["제품ID"],
        "averageLeadTimeDays": df_productvendor["평균리드타임"],
        "standardPrice": df_productvendor["표준단가"],
        "lastReceiptCost": df_productvendor["최종입고단가"],
        "lastReceiptDate": to_date(df_productvendor["최종입고일"]),
        "minOrderQty": df_productvendor["최소주문수량"],
        "maxOrderQty": df_productvendor["최대주문수량"],
        "onOrderQty": df_productvendor["주문진행수량"],
        "unitCode": df_productvendor["단위"].str.strip(),
        "modifiedAt": to_iso(df_productvendor["수정일시"]),
    })
    before = len(supplies)
    supplies = supplies[supplies["supplierId"].isin(active_ids)].copy()
    supplies["supplyKey"] = supplies["supplierId"] + "-" + supplies["productId"]
    print(f"SUPPLIES: 비활성 공급업체 제외로 {before} -> {len(supplies)} 행")
    save(supplies, out_dir, "rels_supplies.csv")


# =============================================================================
# 트랜잭션 데이터 (배치 단위로 지속 적재) — PurchaseOrder, PurchaseOrderLine, SalesOrder,
#                                          WorkOrder, RoutingOperation 노드 5종 +
#                                          HAS_LINE, PLACED_WITH, FOR_PRODUCT, CONTAINS_PRODUCT,
#                                          HAS_OPERATION, PERFORMED_AT, PRODUCES, SCRAPPED_DUE_TO
#                                          관계 8종
#
# 세 가지 모드(0005 ADR "결정 3" 참고) — 모두 아래 _export_transactional_rows()를
# 공유한다. "어느 행을 뽑을지"만 다르고 CSV를 만드는 로직 자체는 동일하기 때문이다.
#   - export_transactional(month)            : 강제 재적재(backfill/reprocessing)
#   - export_transactional_before(as_of)     : 초기 백필
#   - export_transactional_since_last(as_of) : 워터마크 증분(기본 동작)
# =============================================================================

def month_mask(month: str):
    def _mask(date_series):
        return pd.to_datetime(date_series).dt.strftime("%Y-%m") == month
    return _mask


def range_mask(lower: str | None, upper: str):
    def _mask(date_series):
        d = pd.to_datetime(date_series).dt.strftime("%Y-%m-%d")
        result = d <= upper
        if lower is not None:
            result = result & (d > lower)
        return result
    return _mask


def _export_transactional_rows(select_po, select_so, select_wo, out_dir: Path) -> tuple[int, int, int]:
    """select_po/select_so/select_wo: 날짜 컬럼(Series)을 받아 boolean Series를
    반환하는 함수. 이 마스크로 걸러낸 행만 CSV로 뽑는다."""
    df_po_header = read("구매주문_헤더")
    df_po_detail = read("구매주문_상세")
    df_so_header = read("판매주문_헤더")
    df_so_detail = read("판매주문_상세")
    df_workorder = read("생산작업지시")
    df_routing = read("공정순서_라우팅")

    # ---------------- PurchaseOrder / PurchaseOrderLine ----------------
    po_mask = select_po(df_po_header["주문일"])
    po = df_po_header[po_mask]
    po_ids = set(po["구매주문ID"])

    save(pd.DataFrame({
        "purchaseOrderId": po["구매주문ID"],
        "revisionNumber": po["개정번호"],
        "statusCode": po["상태"],
        "employeeId": po["직원ID"],
        "shipMethodId": po["배송방법ID"],
        "orderDate": to_date(po["주문일"]),
        "shipDate": to_date(po["출하일"]),
        "subTotal": po["소계"],
        "taxAmount": po["세액"],
        "freight": po["운임"],
        "modifiedAt": to_iso(po["수정일시"]),
    }), out_dir, "nodes_purchase_order.csv")

    save(pd.DataFrame({
        "purchaseOrderId": po["구매주문ID"],
        "supplierId": po["공급업체ID"],
    }), out_dir, "rels_placed_with.csv")

    pol = df_po_detail[df_po_detail["구매주문ID"].isin(po_ids)]
    save(pd.DataFrame({
        "purchaseOrderLineId": pol["구매주문상세ID"],
        "dueDate": to_date(pol["납기일"]),
        "orderQty": pol["지시수량"],
        "unitPrice": pol["단가"],
        "receivedQty": pol["입고수량"],
        "rejectedQty": pol["불합격수량"],
        "modifiedAt": to_iso(pol["수정일시"]),
    }), out_dir, "nodes_purchase_order_line.csv")

    save(pd.DataFrame({
        "purchaseOrderId": pol["구매주문ID"],
        "purchaseOrderLineId": pol["구매주문상세ID"],
    }), out_dir, "rels_has_line.csv")

    save(pd.DataFrame({
        "purchaseOrderLineId": pol["구매주문상세ID"],
        "productId": pol["제품ID"],
    }), out_dir, "rels_for_product.csv")

    # ---------------- SalesOrder ----------------
    so_mask = select_so(df_so_header["주문일"])
    so = df_so_header[so_mask]
    so_ids = set(so["판매주문ID"])

    save(pd.DataFrame({
        "salesOrderId": so["판매주문ID"],
        "revisionNumber": so["개정번호"],
        "salesOrderNumber": so["판매주문번호"],
        "orderDate": to_date(so["주문일"]),
        "dueDate": to_date(so["납기일"]),
        "shipDate": to_date(so["출하일"]),
        "statusCode": so["상태"],
        "onlineOrder": to_bool(so["온라인주문여부"]),
        "purchaseOrderNumber": so["구매주문번호"],
        "accountNumber": so["계정번호"],
        "customerId": so["고객ID"],
        "salesPersonId": so["영업사원ID"],
        "salesTerritoryId": so["영업지역ID"],
        "shipMethodId": so["배송방법ID"],
        "subTotal": so["소계"],
        "taxAmount": so["세액"],
        "freight": so["운임"],
        "totalDue": so["총액"],
        "rowGuid": so["행GUID"],
        "modifiedAt": to_iso(so["수정일시"]),
    }), out_dir, "nodes_sales_order.csv")

    sol = df_so_detail[df_so_detail["판매주문ID"].isin(so_ids)]
    save(pd.DataFrame({
        "salesOrderId": sol["판매주문ID"],
        "productId": sol["제품ID"],
        "salesOrderLineId": sol["판매주문상세ID"],
        "carrierTrackingNumber": sol["운송장번호"],
        "orderQty": sol["지시수량"],
        "specialOfferId": sol["프로모션ID"],
        "unitPrice": sol["단가"],
        "unitPriceDiscount": sol["단가할인"],
        "lineTotal": sol["금액합계"],
        "rowGuid": sol["행GUID"],
        "modifiedAt": to_iso(sol["수정일시"]),
    }), out_dir, "rels_contains_product.csv")

    # ---------------- WorkOrder / RoutingOperation ----------------
    wo_mask = select_wo(df_workorder["시작일"])
    wo = df_workorder[wo_mask]
    wo_ids = set(wo["작업지시ID"])

    save(pd.DataFrame({
        "workOrderId": wo["작업지시ID"],
        "orderQty": wo["지시수량"],
        "stockedQty": wo["입고수량"],
        "scrappedQty": wo["폐기수량"],
        "startDate": to_date(wo["시작일"]),
        "endDate": to_date(wo["종료일"]),
        "dueDate": to_date(wo["납기일"]),
        "modifiedAt": to_iso(wo["수정일시"]),
    }), out_dir, "nodes_work_order.csv")

    save(pd.DataFrame({
        "workOrderId": wo["작업지시ID"],
        "productId": wo["제품ID"],
    }), out_dir, "rels_produces.csv")

    # 팀 결정 2: 현재 데이터엔 누락 없음 확인됨 -> dropna로 자연히 걸러짐(예외처리 불필요)
    save(pd.DataFrame({
        "workOrderId": wo["작업지시ID"],
        "scrapReasonId": wo["폐기사유ID"],
    }).dropna(), out_dir, "rels_scrapped_due_to.csv")

    routing = df_routing[df_routing["작업지시ID"].isin(wo_ids)].copy()
    routing["routingOperationKey"] = (
        routing["작업지시ID"] + "-" + routing["제품ID"] + "-" + routing["공정순번"]
    )
    save(pd.DataFrame({
        "routingOperationKey": routing["routingOperationKey"],
        "sequence": routing["공정순번"],
        "plannedStartDate": to_date(routing["계획시작일"]),
        "plannedEndDate": to_date(routing["계획종료일"]),
        "actualStartDate": to_date(routing["실제시작일"]),
        "actualEndDate": to_date(routing["실제종료일"]),
        "actualHours": routing["실제소요시간"],
        "plannedCost": routing["계획원가"],
        "actualCost": routing["실제원가"],
        "modifiedAt": to_iso(routing["수정일시"]),
    }), out_dir, "nodes_routing_operation.csv")

    save(pd.DataFrame({
        "workOrderId": routing["작업지시ID"],
        "routingOperationKey": routing["routingOperationKey"],
    }), out_dir, "rels_has_operation.csv")

    save(pd.DataFrame({
        "routingOperationKey": routing["routingOperationKey"],
        "locationId": routing["작업장ID"],
    }), out_dir, "rels_performed_at.csv")

    return len(po), len(so), len(wo)


def export_transactional(month: str, import_dir: Path = IMPORT_DIR) -> None:
    """month: 'YYYY-MM' 형식. 강제 재적재(backfill/reprocessing)용 —
    해당 월에 속하는 트랜잭션만 CSV로 뽑는다."""
    out_dir = import_dir / f"tx_{month}"
    mask = month_mask(month)
    po_n, so_n, wo_n = _export_transactional_rows(mask, mask, mask, out_dir)
    print(f"\n완료: {out_dir} 아래 {month} 트랜잭션 CSV 생성됨 "
          f"(PurchaseOrder {po_n}건, SalesOrder {so_n}건, WorkOrder {wo_n}건)")


def export_transactional_before(as_of: str, import_dir: Path = IMPORT_DIR) -> None:
    """as_of: 'YYYY-MM-DD' 형식. 초기 백필 — as_of 이전 전체 이력을 한 번에 CSV로 뽑는다."""
    out_dir = import_dir / "tx_backfill"
    mask = range_mask(None, as_of)
    po_n, so_n, wo_n = _export_transactional_rows(mask, mask, mask, out_dir)
    print(f"\n완료: {out_dir} 아래 {as_of} 이전 전체 트랜잭션 CSV 생성됨 "
          f"(PurchaseOrder {po_n}건, SalesOrder {so_n}건, WorkOrder {wo_n}건)")


def export_transactional_since_last(as_of: str, import_dir: Path = IMPORT_DIR) -> None:
    """as_of: 'YYYY-MM-DD' 형식. 워터마크 증분 — Neo4j에서 라벨별 MAX(날짜)를
    조회해 하한으로 쓰고, watermark < date <= as_of 범위만 CSV로 뽑는다."""
    out_dir = import_dir / "tx_incremental"
    watermarks = get_watermarks()
    print(f"워터마크: {watermarks}")
    po_mask = range_mask(watermarks["PurchaseOrder"], as_of)
    so_mask = range_mask(watermarks["SalesOrder"], as_of)
    wo_mask = range_mask(watermarks["WorkOrder"], as_of)
    po_n, so_n, wo_n = _export_transactional_rows(po_mask, so_mask, wo_mask, out_dir)
    print(f"\n완료: {out_dir} 아래 워터마크~{as_of} 트랜잭션 CSV 생성됨 "
          f"(PurchaseOrder {po_n}건, SalesOrder {so_n}건, WorkOrder {wo_n}건)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["master", "tx"])
    tx_group = parser.add_mutually_exclusive_group()
    tx_group.add_argument("--month", help="YYYY-MM. 강제 재적재(backfill/reprocessing)")
    tx_group.add_argument("--before", metavar="AS_OF", help="YYYY-MM-DD. 초기 백필(이 날짜 이전 전체 이력)")
    tx_group.add_argument("--since-last", action="store_true", help="워터마크 증분(기본 동작)")
    parser.add_argument("--as-of", help="YYYY-MM-DD. --since-last의 상한(생략 시 오늘 날짜)")
    args = parser.parse_args()

    if args.mode == "master":
        export_master()
        print(f"\n완료: {IMPORT_DIR / 'master'} 아래 마스터 노드/관계 CSV 생성됨 (1회 실행)")
    else:
        if not (args.month or args.before or args.since_last):
            parser.error("tx 모드는 --month / --before / --since-last 중 하나가 필요합니다")
        if args.month:
            export_transactional(args.month)
        elif args.before:
            export_transactional_before(args.before)
        else:
            from datetime import date

            as_of = args.as_of or date.today().strftime("%Y-%m-%d")
            export_transactional_since_last(as_of)
