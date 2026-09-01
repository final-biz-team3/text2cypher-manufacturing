export const COLUMN_LABELS: Record<string, string> = {
  productId: '제품 ID',
  productName: '제품명',
  productNumber: '제품번호',
  safetyStockLevel: '안전재고',
  actualStock: '실제재고',
  shortageQty: '부족수량',
  totalOrderQty: '판매수량',
  supplierId: '업체 ID',
  supplierName: '공급업체명',
  totalRejectedQty: '반려수량',
  suppliedProductCount: '공급 제품 수',
  workOrderId: '작업지시 ID',
  scrappedQty: '폐기수량',
  scrapReasonId: '폐기사유 ID',
  scrapReasonName: '폐기사유',
  locationId: '작업장 ID',
  locationName: '작업장명',
  workOrderCount: '작업지시 수',
  operationCount: '공정 수',
  categoryId: '분류 ID',
  categoryName: '분류명',
  productCount: '제품 수',
  averageListPrice: '평균 정가',
  quantity: '수량',
  shelf: '선반',
  bin: '보관함',
  active: '활성 여부',
  routingOperationKey: '공정 식별키',
  sequence: '공정 순서',
  componentId: '구성품 ID',
  componentName: '구성품명',
  quantityPerAssembly: '단위당 필요수량',
  startDate: '시작일',
  endDate: '종료일',
}

const CURRENCY_KEYS = new Set(['listPrice', 'standardCost', 'averageListPrice'])

export function formatDashboardValue(key: string, value: unknown): string {
  if (value === null || value === undefined || value === '') return '미등록'
  if (typeof value === 'boolean') {
    if (key === 'makeFlag') return value ? '자체 생산' : '외부 구매'
    if (key === 'finishedGoodsFlag') return value ? '완제품' : '비완제품'
    return value ? '활성' : '비활성'
  }
  if (typeof value === 'number') {
    if (CURRENCY_KEYS.has(key)) {
      return new Intl.NumberFormat('ko-KR', {
        style: 'currency',
        currency: 'USD',
        maximumFractionDigits: 2,
      }).format(value)
    }
    return value.toLocaleString('ko-KR', { maximumFractionDigits: 2 })
  }
  if (typeof value === 'string' && /^\d{4}-\d{2}-\d{2}/.test(value)) {
    return new Intl.DateTimeFormat('ko-KR', { dateStyle: 'medium' }).format(new Date(value))
  }
  return String(value)
}

export function entityFromRow(row: Record<string, unknown>) {
  const candidates = [
    ['routing-operation', 'routingOperationKey'],
    ['work-order', 'workOrderId'],
    ['supplier', 'supplierId'],
    ['location', 'locationId'],
    ['scrap-reason', 'scrapReasonId'],
    ['product', 'productId'],
  ] as const
  for (const [type, key] of candidates) {
    const id = row[key]
    if (typeof id === 'string' || typeof id === 'number') return { type, id }
  }
  return null
}
