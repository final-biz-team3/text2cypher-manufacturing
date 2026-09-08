import { Sparkles } from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { AnswerVisualization } from './AnswerVisualization'
import { PathGraphCanvas } from '@/components/graph/PathGraphCanvas'
import type { VisualizationSpec } from '@/lib/schemas'

interface NaturalLanguageAnswerBoxProps {
  answer: string
  visualization?: VisualizationSpec | null
  hasGraphResult?: boolean
  graphRows?: readonly Record<string, unknown>[]
  graphError?: string | null
  graphEmptyReason?: string | null
}

// LLM이 생성한 Markdown 답변을 raw HTML 실행 없이 표시한다.
export function NaturalLanguageAnswerBox({
  answer,
  visualization,
  hasGraphResult,
  graphRows,
  graphError,
  graphEmptyReason,
}: NaturalLanguageAnswerBoxProps) {
  // 노드 하나짜리 그래프는 점 하나만 찍혀서 정보가 없다 - 그래프 없이
  // AI 답변 텍스트만 보여준다.
  const showGraph = hasGraphResult && (graphRows?.length ?? 0) > 1

  return (
    <section
      aria-labelledby="ai-answer-title"
      className="min-w-0 overflow-hidden rounded-md border border-info bg-accent-bg text-[13.5px] leading-relaxed text-text"
    >
      <div className="flex items-center gap-2 border-b border-info/25 px-4 py-2.5">
        <Sparkles className="size-4 text-info" aria-hidden="true" />
        <div>
          <h2 id="ai-answer-title" className="text-[12.5px] font-semibold text-text">
            AI 정리 답변
          </h2>
          <p className="text-[10.5px] text-text-muted">조회된 데이터만 근거로 정리했습니다.</p>
        </div>
      </div>
      <div className="p-4">
        {visualization ? <AnswerVisualization visualization={visualization} /> : null}
        <ReactMarkdown
          remarkPlugins={[remarkGfm]}
          components={{
            h1: ({ children }) => (
              <h3 className="mb-2 mt-4 border-b border-info/20 pb-1.5 text-base font-semibold text-text first:mt-0">
                {children}
              </h3>
            ),
            h2: ({ children }) => (
              <h3 className="mb-2 mt-4 border-b border-info/20 pb-1.5 text-base font-semibold text-text first:mt-0">
                {children}
              </h3>
            ),
            h3: ({ children }) => (
              <h3 className="mb-1.5 mt-3 font-semibold text-text">{children}</h3>
            ),
            p: ({ children }) => <p className="my-2 first:mt-0 last:mb-0">{children}</p>,
            strong: ({ children }) => (
              <strong className="font-semibold text-info">{children}</strong>
            ),
            ul: ({ children }) => (
              <ul className="my-2 list-disc space-y-1 pl-5 marker:text-info">{children}</ul>
            ),
            ol: ({ children }) => (
              <ol className="my-2 list-decimal space-y-1 pl-5 marker:text-info marker:font-semibold">
                {children}
              </ol>
            ),
            hr: () => <hr className="my-3 border-border" />,
            table: ({ children }) => (
              <div className="my-3 max-w-full overflow-x-auto rounded-sm border border-border">
                <table className="min-w-full border-collapse text-left text-xs">{children}</table>
              </div>
            ),
            th: ({ children }) => (
              <th className="border-b border-r border-border bg-panel px-3 py-2 font-semibold last:border-r-0">
                {children}
              </th>
            ),
            td: ({ children }) => (
              <td className="max-w-72 break-words border-b border-r border-border px-3 py-2 align-top last:border-r-0">
                {children}
              </td>
            ),
            a: ({ children }) => <span>{children}</span>,
            code: ({ children }) => (
              <code className="rounded-sm bg-panel px-1 py-0.5 text-[0.92em]">{children}</code>
            ),
          }}
        >
          {answer}
        </ReactMarkdown>
        {showGraph ? (
          <div className="mt-3">
            <PathGraphCanvas
              rows={graphRows ?? []}
              error={graphError ?? null}
              emptyReason={graphEmptyReason ?? null}
            />
          </div>
        ) : null}
      </div>
    </section>
  )
}
