// 자연어 질의 → Cypher 생성/실행 흐름에서 오가는 데이터 모델 정의

// 자가 수정(self-correction) 타임라인 한 단계(생성/실행 시도)
export interface SelfCorrectionStep {
  id: string
  status: 'success' | 'fail' | 'warn'
  title: string
  detail: string
  elapsedMs: number
}

export interface ResultColumn {
  key: string
  label: string
}

export type NodeLabel =
  'Product' | 'Supplier' | 'WorkOrder' | 'RoutingOperation' | 'Location' | 'ScrapReason'

// 지식그래프 스키마 사이드바에 표시되는 노드 타입 정보
export interface SchemaNode {
  label: NodeLabel
  glyph: string
  description: string
  properties: string[]
}

export interface SchemaRelationship {
  name: string
  description: string
}
