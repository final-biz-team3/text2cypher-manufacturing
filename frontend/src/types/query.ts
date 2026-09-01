// 자연어 질의 → Cypher 생성/실행 흐름에서 오가는 데이터 모델 정의

// 자가 수정(self-correction) 타임라인 한 단계(생성/실행 시도)
export interface SelfCorrectionStep {
  id: string
  status: 'success' | 'fail' | 'warn'
  title: string
  detail: string
  elapsedMs?: number
}

export interface ResultColumn {
  key: string
  label: string
}

export interface RetryAttempt {
  query: string
  error: string | null
}

// /chat 응답이나 대화기록 항목을 화면에 뿌릴 수 있게 정리한 형태
export interface DisplayResult {
  query: string
  answer: string
  sql: string | null
  cypher: string | null
  columns: ResultColumn[]
  rows: Record<string, string>[]
  hasGraphResult: boolean
  graphRows: Record<string, unknown>[]
  graphError: string | null
  graphEmptyReason: string | null
  sqlAttempts: RetryAttempt[]
  cypherAttempts: RetryAttempt[]
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
