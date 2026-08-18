import type { NodeLabel } from '@/types/query'

// 노드 라벨(Lot/Process/...)별 배지·그래프 색상을 한 곳에서 관리한다
export const NODE_COLOR_CLASS: Record<NodeLabel, string> = {
  Lot: 'bg-node-lot',
  Process: 'bg-node-process',
  Equipment: 'bg-node-equipment',
  Material: 'bg-node-material',
  Defect: 'bg-node-defect',
}
