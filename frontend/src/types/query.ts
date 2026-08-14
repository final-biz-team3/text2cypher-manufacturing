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

// 하나의 질의에 대한 전체 결과(자연어 답변 + Cypher + 표 데이터 + 자가수정 이력)
export interface QueryResult {
  answer: string
  cypher: string
  columns: ResultColumn[]
  rows: Record<string, string>[]
  timeline: SelfCorrectionStep[]
}

export type NodeLabel = 'Lot' | 'Process' | 'Equipment' | 'Material' | 'Defect'

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

// 사이드바 "질문 이력" 탭에 표시되는 과거 질의 기록
export interface HistoryItem {
  id: string
  question: string
  submittedAt: number
}
