import { useState } from 'react'
import { ChevronDown, Database } from 'lucide-react'
import { ResultsTable } from '@/components/result/ResultsTable'
import type { DisplayResult } from '@/types/query'

// 관계 그래프는 답변박스(NaturalLanguageAnswerBox)로 옮겼다 - 여기는
// 원본 표만 보여준다.
type ResultEvidencePanelProps = Pick<DisplayResult, 'columns' | 'rows'>

export function ResultEvidencePanel({ columns, rows }: ResultEvidencePanelProps) {
  const [open, setOpen] = useState(false)
  const hasTable = columns.length > 0

  if (!hasTable) return null

  return (
    <section className="overflow-hidden rounded-md border border-border bg-panel">
      <button
        type="button"
        className="flex w-full items-center justify-between gap-3 px-4 py-3 text-left transition-colors hover:bg-panel-2 focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring/30"
        aria-expanded={open}
        onClick={() => setOpen((current) => !current)}
      >
        <span className="flex min-w-0 items-center gap-2.5">
          <Database className="size-4 shrink-0 text-text-muted" aria-hidden="true" />
          <span>
            <span className="block text-[12.5px] font-semibold text-text">조회 근거 데이터</span>
            <span className="mt-0.5 block text-[10.5px] text-text-muted">
              AI 정리 답변에 사용된 원본 표
            </span>
          </span>
        </span>
        <ChevronDown
          className={`size-4 shrink-0 text-text-muted transition-transform ${open ? 'rotate-180' : ''}`}
          aria-hidden="true"
        />
      </button>
      {open ? (
        <div className="flex flex-col gap-4 border-t border-border p-4">
          <ResultsTable columns={columns} rows={rows} />
        </div>
      ) : null}
    </section>
  )
}
