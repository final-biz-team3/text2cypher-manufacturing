import { useState } from 'react'
import { ChevronDown, Database } from 'lucide-react'
import { PathGraphCanvas } from '@/components/graph/PathGraphCanvas'
import { ResultsTable } from '@/components/result/ResultsTable'
import type { DisplayResult } from '@/types/query'

type ResultEvidencePanelProps = Pick<
  DisplayResult,
  | 'columns'
  | 'rows'
  | 'hasGraphResult'
  | 'graphRows'
  | 'graphError'
  | 'graphEmptyReason'
  | 'sections'
>

export function ResultEvidencePanel({
  columns,
  rows,
  hasGraphResult,
  graphRows,
  graphError,
  graphEmptyReason,
  sections,
}: ResultEvidencePanelProps) {
  const [open, setOpen] = useState(false)
  const hasTable = columns.length > 0

  if (!hasGraphResult && !hasTable && !sections?.length) return null

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
              {sections
                ? '답변과 동일한 최종 조회 결과'
                : 'AI 정리 답변에 사용된 원본 표와 관계 그래프'}
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
          {hasGraphResult ? (
            <PathGraphCanvas rows={graphRows} error={graphError} emptyReason={graphEmptyReason} />
          ) : null}
          {hasTable ? <ResultsTable columns={columns} rows={rows} /> : null}
          {sections?.map((section) => (
            <div key={section.id} className="flex flex-col gap-2">
              <h3 className="text-sm font-semibold">{section.title}</h3>
              {section.rows.length ? (
                <ResultsTable columns={section.columns} rows={section.rows} />
              ) : (
                <p className="text-sm text-text-muted">지정한 조건에 해당하는 데이터가 없습니다.</p>
              )}
              {section.truncated && (
                <p className="text-sm text-text-muted">
                  조회 한도에 도달해 일부 결과만 표시합니다.
                </p>
              )}
            </div>
          ))}
        </div>
      ) : null}
    </section>
  )
}
