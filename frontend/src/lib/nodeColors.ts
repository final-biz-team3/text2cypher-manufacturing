import type { NodeLabel } from '@/types/query'

export const NODE_COLOR_CLASS: Record<NodeLabel, string> = {
  Lot: 'bg-node-lot',
  Process: 'bg-node-process',
  Equipment: 'bg-node-equipment',
  Material: 'bg-node-material',
  Defect: 'bg-node-defect',
}
