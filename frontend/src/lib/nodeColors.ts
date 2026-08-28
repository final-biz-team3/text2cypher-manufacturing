import type { NodeLabel } from '@/types/query'

// 노드 라벨(Product/Supplier/...)별 배지·그래프 색상을 한 곳에서 관리한다
export const NODE_COLOR_CLASS: Record<NodeLabel, string> = {
  Product: 'bg-node-product',
  Supplier: 'bg-node-supplier',
  WorkOrder: 'bg-node-workorder',
  RoutingOperation: 'bg-node-routingoperation',
  Location: 'bg-node-location',
  ScrapReason: 'bg-node-scrapreason',
}
