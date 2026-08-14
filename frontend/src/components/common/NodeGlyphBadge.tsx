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

// 노드 타입을 나타내는 원형 글리프 배지(스키마 사이드바, 예시 질문 카드 등에서 공통 사용)
export function NodeGlyphBadge({ nodeLabel, glyph, size }: NodeGlyphBadgeProps) {
  return (
    <span
      className={`flex shrink-0 items-center justify-center rounded-full font-bold text-white ${SIZE_CLASS[size]} ${NODE_COLOR_CLASS[nodeLabel]}`}
    >
      {glyph}
    </span>
  )
}
