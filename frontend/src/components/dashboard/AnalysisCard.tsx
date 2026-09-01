import { List, MessageSquareText } from 'lucide-react'
import { Button } from '@/components/ui/button'
import type { DashboardCard } from '@/lib/dashboard'
import { DashboardDataTable } from './DashboardDataTable'

interface AnalysisCardProps {
  card: DashboardCard
  selectedEntityId?: string | number | null
  onOpenAll: (trigger: HTMLElement) => void
  onAsk: () => void
  onSelectRow: (row: Record<string, unknown>, trigger: HTMLElement) => void
}

export function AnalysisCard({
  card,
  selectedEntityId,
  onOpenAll,
  onAsk,
  onSelectRow,
}: AnalysisCardProps) {
  return (
    <section className="flex min-h-72 min-w-0 flex-col overflow-hidden rounded-md border border-border bg-panel shadow-[0_1px_2px_rgba(15,23,42,0.03)]">
      <div className="flex items-center justify-between border-b border-border px-4 py-3">
        <div className="flex min-w-0 items-center gap-2">
          <h2 className="truncate text-[13px] font-semibold text-text">{card.title}</h2>
          <span
            className={`size-1.5 rounded-full ${card.status === 'ready' ? 'bg-info' : 'bg-fail'}`}
          />
        </div>
        <span className="text-[10.5px] text-text-faint">총 {card.total.toLocaleString()}건</span>
      </div>
      <div className="min-h-0 flex-1">
        {card.status === 'error' ? (
          <div className="flex h-full min-h-44 items-center justify-center px-4 text-center text-[12px] text-fail">
            이 카드의 정보를 불러오지 못했습니다.
          </div>
        ) : (
          <DashboardDataTable
            compact
            columns={card.columns}
            rows={card.rows}
            entityIdField={card.entityIdField}
            selectedId={selectedEntityId}
            onRowSelect={card.entityType ? onSelectRow : undefined}
          />
        )}
      </div>
      <div className="flex items-center border-t border-border px-2 py-1.5">
        <Button
          type="button"
          variant="ghost"
          size="sm"
          className="flex-1 justify-center text-info"
          onClick={(event) => onOpenAll(event.currentTarget)}
        >
          <List />
          전체 보기
        </Button>
        <span className="h-4 w-px bg-border" aria-hidden="true" />
        <Button
          type="button"
          variant="ghost"
          size="sm"
          className="flex-1 justify-center text-info"
          onClick={onAsk}
        >
          <MessageSquareText />
          AI에게 질문
        </Button>
      </div>
    </section>
  )
}
