import type { SchemaNode, SchemaRelationship } from '@/types/query'

// 지식그래프 스키마 노드/관계 타입 정의
export const SCHEMA_NODES: SchemaNode[] = [
  {
    label: 'Lot',
    glyph: 'L',
    description: '생산 배치 단위',
    properties: ['lot_id', 'product_code', 'created_at'],
  },
  {
    label: 'Process',
    glyph: 'P',
    description: '공정 단계',
    properties: ['process_name', 'sequence'],
  },
  { label: 'Equipment', glyph: 'EQ', description: '설비', properties: ['equipment_id', 'line'] },
  {
    label: 'Material',
    glyph: 'M',
    description: '투입 자재',
    properties: ['material_code', 'lot_no'],
  },
  {
    label: 'Defect',
    glyph: 'D',
    description: '불량 기록',
    properties: ['defect_code', 'severity', 'detected_at'],
  },
]

export const RELATIONSHIPS: SchemaRelationship[] = [
  { name: 'FOLLOWS', description: '공정 순서' },
  { name: 'PROCESSED_AT', description: '설비 투입' },
  { name: 'HAS_DEFECT', description: '불량 발생' },
  { name: 'CONSUMES', description: '자재 소모' },
]
