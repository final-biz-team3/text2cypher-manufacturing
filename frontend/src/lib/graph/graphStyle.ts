const CATEGORY_COLORS: Record<string, string> = {
  Product: '#5b8fc9',
  Supplier: '#55aa8c',
  WorkOrder: '#e5b85f',
  RoutingOperation: '#b88bbb',
  Location: '#70b8d3',
  ScrapReason: '#df7d72',
}

const RELATIONSHIP_STYLES: Record<string, { label: string; color: string }> = {
  SUPPLIES: { label: '공급', color: '#0f8f72' },
  REQUIRES_COMPONENT: { label: 'BOM 연결', color: '#6f63b6' },
  PRODUCES: { label: '생산', color: '#c0841a' },
  HAS_OPERATION: { label: '공정 포함', color: '#9a62a3' },
  PERFORMED_AT: { label: '작업장 수행', color: '#2787a9' },
  SCRAPPED_DUE_TO: { label: '폐기 사유', color: '#c75f55' },
}

export function colorForCategory(category: string): string {
  return CATEGORY_COLORS[category] ?? '#8b93a1'
}

export function labelForRelationship(relationshipType: string): string {
  return RELATIONSHIP_STYLES[relationshipType]?.label ?? relationshipType
}

export function colorForRelationship(relationshipType: string): string {
  return RELATIONSHIP_STYLES[relationshipType]?.color ?? '#66717d'
}
