import { useEffect, useState } from 'react'
import { PrismLight as SyntaxHighlighter } from 'react-syntax-highlighter'
import cypher from 'react-syntax-highlighter/dist/esm/languages/prism/cypher'
import sql from 'react-syntax-highlighter/dist/esm/languages/prism/sql'
import { vscDarkPlus } from 'react-syntax-highlighter/dist/esm/styles/prism'
import { Button } from '@/components/ui/button'

SyntaxHighlighter.registerLanguage('sql', sql)
SyntaxHighlighter.registerLanguage('cypher', cypher)

interface GeneratedQuery {
  label: string
  language: 'sql' | 'cypher'
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
            <QueryBlock
              key={item.label}
              label={item.label}
              language={item.language}
              query={item.query}
            />
          ))}
        </div>
      )}
    </aside>
  )
}

function QueryBlock({ label, language, query }: GeneratedQuery) {
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
      {/* 코드 에디터 창처럼 보이도록 상단바에 macOS 스타일 점 3개 + 언어 라벨을 둔다 */}
      <div className="flex items-center justify-between border-b border-code-text/10 bg-code px-3 py-2">
        <div className="flex items-center gap-3">
          <div className="flex gap-1.5">
            <span className="size-2.5 rounded-full bg-fail" />
            <span className="size-2.5 rounded-full bg-warn" />
            <span className="size-2.5 rounded-full bg-success" />
          </div>
          <span className="text-[11px] font-semibold tracking-wide text-code-text/70 uppercase">
            {label}
          </span>
        </div>
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={handleCopy}
          className="h-6 border-code-text/20 bg-code px-2 text-[11px] text-code-text hover:bg-code-text hover:text-code"
        >
          {copied ? '복사됨' : '복사'}
        </Button>
      </div>
      <div className="overflow-x-auto bg-code">
        <SyntaxHighlighter
          language={language}
          style={vscDarkPlus}
          showLineNumbers
          customStyle={{
            margin: 0,
            padding: '0.75rem',
            background: 'transparent',
            fontSize: '12px',
            lineHeight: 1.6,
          }}
          lineNumberStyle={{ opacity: 0.4, minWidth: '2em' }}
        >
          {query}
        </SyntaxHighlighter>
      </div>
    </div>
  )
}
