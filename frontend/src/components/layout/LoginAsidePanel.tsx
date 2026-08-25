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
          Cypher를 몰라도 한국어로 질문하면 공정 지식그래프에서 다중 홉 원인 경로를 추적하고 집계
          결과를 확인할 수 있습니다.
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
