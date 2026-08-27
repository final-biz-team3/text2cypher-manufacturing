import { useEffect, useState } from 'react'
import { Button } from '@/components/ui/button'

interface GeneratedQuery {
  label: string
  query: string
}

interface GeneratedQueryPanelProps {
  queries: GeneratedQuery[]
  collapsed: boolean
  onToggleCollapsed: () => void
}

// 생성된 SQL/Cypher 쿼리를 보여주고 복사할 수 있는 우측 슬라이드 패널.
// tool_plan에 sql/graph가 모두 있으면 둘 다 나열한다.
export function GeneratedQueryPanel({
  queries,
  collapsed,
  onToggleCollapsed,
}: GeneratedQueryPanelProps) {
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
        aria-label={collapsed ? '생성된 쿼리 패널 펼치기' : '생성된 쿼리 패널 접기'}
      >
        {collapsed ? '◂' : '▸'}
      </button>
      {collapsed ? (
        <div className="flex flex-1 items-center justify-center">
          <span className="[writing-mode:vertical-rl] text-[11px] font-semibold uppercase tracking-wide text-text-faint">
            생성된 쿼리
          </span>
        </div>
      ) : (
        <div className="flex flex-1 flex-col overflow-y-auto">
          {queries.map((item) => (
            <QueryBlock key={item.label} label={item.label} query={item.query} />
          ))}
        </div>
      )}
    </aside>
  )
}

function QueryBlock({ label, query }: GeneratedQuery) {
  const [copied, setCopied] = useState(false)

  // "복사됨" 표시는 일정 시간 뒤 자동으로 사라진다. 컴포넌트가 언마운트된 뒤
  // 타이머가 실행되지 않도록 useEffect의 cleanup으로 정리한다.
  useEffect(() => {
    if (!copied) return
    const timer = setTimeout(() => setCopied(false), 1400)
    return () => clearTimeout(timer)
  }, [copied])

  const handleCopy = () => {
    if (!navigator.clipboard?.writeText) {
      // 비보안 컨텍스트(HTTP)나 미지원 브라우저에서는 clipboard API 자체가 없다
      return
    }
    navigator.clipboard.writeText(query).then(
      () => setCopied(true),
      () => {
        // 클립보드 쓰기 실패(권한 거부 등) - 성공 표시를 띄우지 않는다
      },
    )
  }

  return (
    <div className="flex flex-col border-b border-border">
      <div className="flex items-center justify-between px-3 py-2">
        <span className="text-[11px] font-semibold uppercase text-text-faint">{label}</span>
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
      <pre className="overflow-auto bg-code p-3 font-mono text-[12px] leading-relaxed text-code-text">
        {query}
      </pre>
    </div>
  )
}
