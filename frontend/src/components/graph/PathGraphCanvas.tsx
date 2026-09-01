import { lazy, Suspense } from 'react'

const SigmaGraph = lazy(() =>
  import('./SigmaGraph').then((module) => ({ default: module.SigmaGraph })),
)

interface PathGraphCanvasProps {
  rows: readonly Record<string, unknown>[]
  error: string | null
  emptyReason: string | null
}

// 그래프 진입점을 유지하면서 API 상태와 Sigma 렌더링을 분리한다.
export function PathGraphCanvas({ rows, error, emptyReason }: PathGraphCanvasProps) {
  if (error) {
    return (
      <section
        className="flex h-[160px] flex-col items-center justify-center gap-1 rounded-lg border border-fail/30 bg-panel-2 px-4 text-center"
        aria-label="지식그래프 오류"
        role="alert"
      >
        <p className="text-[12.5px] font-semibold text-fail">그래프를 불러오지 못했습니다.</p>
        <p className="text-[11px] text-text-muted">{error}</p>
      </section>
    )
  }

  if (rows.length === 0) {
    return (
      <section
        className="flex h-[160px] flex-col items-center justify-center gap-1 rounded-lg border border-border bg-panel-2 px-4 text-center"
        aria-label="빈 지식그래프"
      >
        <p className="text-[12.5px] font-semibold text-text">표시할 그래프 결과가 없습니다.</p>
        {emptyReason ? <p className="text-[11px] text-text-muted">{emptyReason}</p> : null}
      </section>
    )
  }

  return (
    <Suspense
      fallback={
        <div className="flex h-[320px] items-center justify-center rounded-lg border border-border bg-panel-2 text-[12.5px] text-text-faint">
          그래프 렌더러를 불러오는 중입니다…
        </div>
      }
    >
      <SigmaGraph rows={rows} />
    </Suspense>
  )
}
