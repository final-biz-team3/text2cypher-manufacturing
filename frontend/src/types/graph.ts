import type { Attributes } from 'graphology-types'

export type GraphPropertyValue = unknown

export interface GraphNodeAttributes extends Attributes {
  label: string
  category: string
  categoryLabel: string
  displayTitle: string
  displayMeta: string
  color: string
  size: number
  x: number
  y: number
  originalId?: string
  hidden?: boolean
  properties: Record<string, GraphPropertyValue>
}

export interface GraphEdgeAttributes extends Attributes {
  label?: string
  relationshipType?: string
  color?: string
  size?: number
  weight?: number
  hidden?: boolean
  properties: Record<string, GraphPropertyValue>
}

export interface GraphAttributes extends Attributes {
  source: 'neo4j-api'
}

export interface NormalizedGraphNode {
  key: string
  attributes: GraphNodeAttributes
  hasPosition: boolean
}

export interface NormalizedGraphEdge {
  key?: string
  source: string
  target: string
  relationshipType: string
  attributes: GraphEdgeAttributes
}

export interface GraphBuildIssue {
  kind: 'invalid-node' | 'invalid-edge' | 'missing-endpoint'
  message: string
}
