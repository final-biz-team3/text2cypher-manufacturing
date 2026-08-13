import { NODE_COLOR_CLASS } from '@/lib/nodeColors'
import type { NodeLabel } from '@/types/query'

interface NodeGlyphBadgeProps {
  nodeLabel: NodeLabel
  glyph: string
  size: 11 | 18
}

const SIZE_CLASS: Record<NodeGlyphBadgeProps['size'], string> = {
  11: 'size-[11px] text-[7px]',
  18: 'size-[18px] text-[9px]',
}

export function NodeGlyphBadge({ nodeLabel, glyph, size }: NodeGlyphBadgeProps) {
  return (
    <span
      className={`flex shrink-0 items-center justify-center rounded-full font-bold text-white ${SIZE_CLASS[size]} ${NODE_COLOR_CLASS[nodeLabel]}`}
    >
      {glyph}
    </span>
  )
}
