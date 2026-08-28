import type { SchemaNode, SchemaRelationship } from '@/types/query'

// 지식그래프 스키마 노드/관계 타입 정의 (schema/graph_schema.yaml 기준)
export const SCHEMA_NODES: SchemaNode[] = [
  {
    label: 'Product',
    glyph: 'P',
    description: '제품 · 부품 · 완제품',
    properties: ['productId', 'name', 'sellableFinishedGood'],
  },
  {
    label: 'Supplier',
    glyph: 'S',
    description: '공급업체',
    properties: ['supplierId', 'name', 'active'],
  },
  {
    label: 'WorkOrder',
    glyph: 'WO',
    description: '생산 작업지시',
    properties: ['workOrderId'],
  },
  {
    label: 'RoutingOperation',
    glyph: 'RO',
    description: '작업 공정',
    properties: ['sequence'],
  },
  {
    label: 'Location',
    glyph: 'L',
    description: '작업장',
    properties: ['locationId', 'name'],
  },
  {
    label: 'ScrapReason',
    glyph: 'SR',
    description: '폐기 사유',
    properties: ['scrapReasonId', 'name'],
  },
]

export const RELATIONSHIPS: SchemaRelationship[] = [
  { name: 'SUPPLIES', description: '공급업체가 부품을 공급함' },
  { name: 'REQUIRES_COMPONENT', description: 'BOM 상 상위 조립품이 하위 부품을 필요로 함' },
  { name: 'PRODUCES', description: '작업지시가 제품을 생산함' },
  { name: 'HAS_OPERATION', description: '작업지시가 공정을 포함함' },
  { name: 'PERFORMED_AT', description: '공정이 작업장에서 수행됨' },
  { name: 'SCRAPPED_DUE_TO', description: '작업지시가 폐기 사유로 이어짐' },
]
