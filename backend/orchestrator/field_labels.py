"""composed_result 컬럼 키(camelCase) -> 한글 라벨 매핑.

DB 필드명은 영어인데 답변은 한국어로 그 개념을 설명해야 한다. 이 표는
generate_answer.py에서 세 곳에 쓰인다: (1) LLM 답변 생성이 끝내 실패했을 때
rows 값을 그대로 옮기는 폴백 답변의 라벨, (2) 통화/개수 단위 서식(정가→"약
$", 활성 공급업체 수→"...곳")을 어떤 필드에 적용할지 판단하는 기준,
(3) 프롬프트에 "이 필드는 이 라벨을 쓰라"고 알려주는 힌트. highlighted의
title/metrics.value 근거 검증(_check_items_grounding)은 rows 값과 정확히
대조하지, 이 표를 쓰지 않는다.

매핑에 없는 키는 그대로 두면 된다(호출부가 폴백을 알아서 처리한다 - 보통
영문 키가 그대로 노출된다).

값은 대부분 schema/sql_schema.yaml의 columns.*.aliases(팀이 이미 정의해둔
공식 한글 별칭)에서 그대로 가져왔다 - 임의로 새로 짓지 않았다.
"""

FIELD_LABELS: dict[str, str] = {
    "listPrice": "정가",
    "standardCost": "표준원가",
    "priceCostGap": "정가-원가 차액",
    "activeSupplierCount": "활성 공급업체 수",
    "purchasedProductCount": "외부 구매 부품 수",
    "productCount": "제품 수",
    "safetyStockLevel": "안전재고",
    "actualStock": "실제재고",
    "shortageQty": "부족 수량",
    "totalOrderQty": "판매량",
    "totalRejectedQty": "반려 수량",
    "scrappedQty": "폐기 수량",
    "totalScrappedQty": "총 폐기 수량",
    "suppliedProductCount": "공급 부품 종류 수",
    "averageListPrice": "평균 정가",
    "workOrderCount": "작업지시 수",
    "sharedComponentCount": "공통 부품 수",
    "quantityPerAssembly": "조립당 필요 수량",
    "productName": "제품명",
    "supplierName": "공급업체명",
    "categoryName": "분류명",
    "locationName": "작업장명",
    "scrapReasonName": "폐기사유",
    "componentName": "부품명",
    "finishedProductName": "완제품명",
    "rootProductName": "완제품명",
    # 아래는 schema/sql_schema.yaml의 columns.*.aliases를 그대로 옮겨왔다 -
    # 위 항목들과 달리 대부분 식별자(ID)나 개별 행 단위 필드라, 목록/상세
    # 조회 답변에서 자주 등장한다.
    "productId": "제품 ID",
    "productNumber": "제품번호",
    "makeFlag": "자체 생산 여부",
    "color": "색상",
    "size": "크기",
    "sellEndDate": "판매 종료일",
    "quantity": "재고 수량",
    "shelf": "선반",
    "bin": "보관함",
    "locationId": "작업장 ID",
    "supplierId": "공급업체 ID",
    "active": "활성 여부",
    "categoryId": "제품 분류 ID",
    "subcategoryId": "제품 하위 분류 ID",
    "subcategoryName": "제품 하위 분류명",
    "bomId": "BOM ID",
    "startDate": "BOM 유효 시작일",
    "endDate": "BOM 유효 종료일",
    "orderQty": "판매 주문 수량",
    "rejectedQty": "반려 수량",
    "workOrderId": "작업지시 ID",
    "scrapReasonId": "폐기 사유 ID",
    "sequence": "공정 순서",
}
