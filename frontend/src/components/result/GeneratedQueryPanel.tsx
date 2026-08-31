import { useEffect, useState } from 'react'
import { PrismLight as SyntaxHighlighter } from 'react-syntax-highlighter'
import cypher from 'react-syntax-highlighter/dist/esm/languages/prism/cypher'
import sql from 'react-syntax-highlighter/dist/esm/languages/prism/sql'
import { vscDarkPlus } from 'react-syntax-highlighter/dist/esm/styles/prism'
import { Button } from '@/components/ui/button'
import { SelfCorrectionTimeline } from '@/components/result/SelfCorrectionTimeline'
import type { SelfCorrectionStep } from '@/types/query'

SyntaxHighlighter.registerLanguage('sql', sql)
SyntaxHighlighter.registerLanguage('cypher', cypher)

interface GeneratedQuery {
  label: string
  language: 'sql' | 'cypher'
  query: string
}

interface GeneratedQueryPanelProps {
  queries: GeneratedQuery[]
  sqlAttempts: SelfCorrectionStep[]
  cypherAttempts: SelfCorrectionStep[]
  collapsed: boolean
  onToggleCollapsed: () => void
}

// 자기수정 타임라인(위) + 생성된 SQL/Cypher 쿼리(아래)를 함께 보여주는 우측 슬라이드 패널.
// tool_plan에 sql/graph가 모두 있으면 둘 다 나열한다.
export function GeneratedQueryPanel({
  queries,
  sqlAttempts,
  cypherAttempts,
  collapsed,
  onToggleCollapsed,
}: GeneratedQueryPanelProps) {
  const hasTimeline = sqlAttempts.length > 0 || cypherAttempts.length > 0
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
          {hasTimeline ? (
            <div className="flex flex-col gap-3 border-b border-border p-4">
              <p className="text-[12.5px] font-semibold text-text">자기수정 타임라인</p>
              {sqlAttempts.length > 0 ? (
                <div>
                  <p className="mb-1.5 text-[11px] font-semibold uppercase text-text-faint">
                    SQL 시도
                  </p>
                  <SelfCorrectionTimeline steps={sqlAttempts} />
                </div>
              ) : null}
              {cypherAttempts.length > 0 ? (
                <div>
                  <p className="mb-1.5 text-[11px] font-semibold uppercase text-text-faint">
                    Cypher 시도
                  </p>
                  <SelfCorrectionTimeline steps={cypherAttempts} />
                </div>
              ) : null}
            </div>
          ) : null}
          {queries.length > 0 ? (
            <div className="flex flex-col gap-3 p-4">
              {queries.map((item) => (
                <QueryBlock
                  key={item.label}
                  label={item.label}
                  language={item.language}
                  query={item.query}
                />
              ))}
            </div>
          ) : null}
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
    <div className="flex flex-col overflow-hidden rounded-lg border border-border">
      <div className="flex items-center justify-between border-b border-code-text/10 bg-code px-3 py-2">
        <span className="text-[11px] font-semibold tracking-wide text-code-text/70 uppercase">
          {label}
        </span>
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
      {/* react-syntax-highlighter는 showLineNumbers+wrapLongLines를 함께 쓰면 테마의
          code[class*="language-"] 기본 스타일(white-space: pre)이 wrapLongLines가
          주려는 pre-wrap을 인라인 스타일 병합 순서상 다시 덮어써 줄바꿈이 안 먹는다
          (react-syntax-highlighter#396류 이슈) - !important로 강제한다. */}
      <div className="query-code-block bg-code">
        <SyntaxHighlighter
          language={language}
          style={vscDarkPlus}
          showLineNumbers
          wrapLongLines
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
