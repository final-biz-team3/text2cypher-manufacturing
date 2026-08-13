import { useState } from 'react'
import { Button } from '@/components/ui/button'

interface CypherSlidePanelProps {
  cypher: string
  collapsed: boolean
  onToggleCollapsed: () => void
}

export function CypherSlidePanel({ cypher, collapsed, onToggleCollapsed }: CypherSlidePanelProps) {
  const [copied, setCopied] = useState(false)

  const handleCopy = () => {
    navigator.clipboard.writeText(cypher).then(
      () => {
        setCopied(true)
        setTimeout(() => setCopied(false), 1400)
      },
      () => {
        // 클립보드 쓰기 실패(권한 거부, 비보안 컨텍스트 등) - 성공 표시를 띄우지 않는다
      },
    )
  }

  return (
    <aside
      className={`flex shrink-0 flex-col border-l border-border bg-panel transition-[width] duration-200 ${
        collapsed ? 'w-[40px]' : 'w-[360px]'
      }`}
    >
      <button
        type="button"
        onClick={onToggleCollapsed}
        className="flex h-10 shrink-0 items-center justify-center border-b border-border text-text-faint hover:text-text"
        aria-expanded={!collapsed}
        aria-label={collapsed ? '생성된 Cypher 패널 펼치기' : '생성된 Cypher 패널 접기'}
      >
        {collapsed ? '◂' : '▸'}
      </button>
      {collapsed ? (
        <div className="flex flex-1 items-center justify-center">
          <span className="[writing-mode:vertical-rl] text-[11px] font-semibold uppercase tracking-wide text-text-faint">
            생성된 Cypher
          </span>
        </div>
      ) : (
        <div className="flex flex-1 flex-col overflow-hidden">
          <div className="flex items-center justify-between border-b border-border px-3 py-2">
            <span className="text-[11px] font-semibold uppercase text-text-faint">
              생성된 Cypher
            </span>
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={handleCopy}
              className="h-6 px-2 text-[11px]"
            >
              {copied ? '복사됨' : '복사'}
            </Button>
          </div>
          <pre className="flex-1 overflow-auto bg-code p-3 font-mono text-[12px] leading-relaxed text-code-text">
            {cypher}
          </pre>
        </div>
      )}
    </aside>
  )
}
