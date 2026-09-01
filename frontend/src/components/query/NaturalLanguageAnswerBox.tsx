import { Sparkles } from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

interface NaturalLanguageAnswerBoxProps {
  answer: string
}

// LLM이 생성한 Markdown 답변을 raw HTML 실행 없이 표시한다.
export function NaturalLanguageAnswerBox({ answer }: NaturalLanguageAnswerBoxProps) {
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
        <ReactMarkdown
          remarkPlugins={[remarkGfm]}
          components={{
            h1: ({ children }) => <h3 className="mb-2 mt-4 text-base font-semibold">{children}</h3>,
            h2: ({ children }) => <h3 className="mb-2 mt-4 text-base font-semibold">{children}</h3>,
            h3: ({ children }) => <h3 className="mb-1.5 mt-3 font-semibold">{children}</h3>,
            p: ({ children }) => <p className="my-2 first:mt-0 last:mb-0">{children}</p>,
            ul: ({ children }) => <ul className="my-2 list-disc space-y-1 pl-5">{children}</ul>,
            ol: ({ children }) => <ol className="my-2 list-decimal space-y-1 pl-5">{children}</ol>,
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
      </div>
    </section>
  )
}
