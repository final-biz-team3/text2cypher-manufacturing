import { SCHEMA_NODES } from '@/lib/schemaNodes'
import { NodeGlyphBadge } from '@/components/common/NodeGlyphBadge'

// 로그인 화면 우측 컨텍스트 패널: 도구 설명 + 스키마 노드 목록을 보여준다
export function LoginAsidePanel() {
  return (
    <aside className="hidden w-[380px] shrink-0 flex-col justify-center gap-6 border-l border-border bg-panel px-8 py-8 lg:flex">
      <div>
        <div className="mb-2 text-[11px] font-bold uppercase tracking-[0.04em] text-text-faint">
          이 도구로 할 수 있는 것
        </div>
        <p className="text-sm leading-relaxed text-text-muted">
          재고, 가격, 부품, 공정, 공급업체 등 필요한 정보를 한 곳에서 확인할 수 있습니다. 데이터 간
          복잡한 관계는 그래프 기반으로 추적하여 원인과 영향을 분석할 수 있습니다.
        </p>
      </div>
      <div>
        <div className="mb-2.5 text-[11px] font-bold uppercase tracking-[0.04em] text-text-faint">
          연결된 그래프 스키마
        </div>
        <ul className="flex flex-col gap-2">
          {SCHEMA_NODES.map((node) => (
            <li key={node.label} className="flex items-center gap-2.5 text-[13px]">
              <NodeGlyphBadge nodeLabel={node.label} glyph={node.glyph} size={18} />
              <span className="font-semibold text-text">{node.label}</span>
              <span className="text-xs text-text-faint">{node.description}</span>
            </li>
          ))}
        </ul>
      </div>
    </aside>
  )
}
