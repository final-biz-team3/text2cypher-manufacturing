"""composed_result 컬럼 키(camelCase) -> 한글 라벨 매핑.

DB 필드명은 영어인데 답변은 한국어로 그 개념을 설명해야 해서, 자유 문장의
근거 검증이 "표준원가"처럼 원문에 없는(하지만 정당한) 한글 개념어를 계속
근거 없음으로 오탐하는 문제가 있었다. 이 표는 실제로 답변 데이터에 존재하는
필드에 한해 그 필드의 한글 라벨을 근거 검증의 허용 대상으로 추가하는 데
쓰인다 - 필드 자체가 없으면 라벨도 허용되지 않으므로, 만들어낸 개념어까지
통과시키진 않는다.

매핑에 없는 키는 그대로 두면 된다(호출부가 폴백을 알아서 처리한다).
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
}
