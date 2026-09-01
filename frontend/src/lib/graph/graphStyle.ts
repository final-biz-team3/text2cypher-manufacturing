const CATEGORY_COLORS: Record<string, string> = {
  Product: '#5b8fc9',
  Supplier: '#55aa8c',
  WorkOrder: '#e5b85f',
  RoutingOperation: '#b88bbb',
  Location: '#70b8d3',
  ScrapReason: '#df7d72',
}

export function colorForCategory(category: string): string {
  return CATEGORY_COLORS[category] ?? '#8b93a1'
}
