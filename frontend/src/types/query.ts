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

export interface QueryResult {
  answer: string
  cypher: string
  columns: ResultColumn[]
  rows: Record<string, string>[]
  timeline: SelfCorrectionStep[]
}

export type NodeLabel = 'Lot' | 'Process' | 'Equipment' | 'Material' | 'Defect'

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
