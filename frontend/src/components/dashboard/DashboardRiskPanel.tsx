import { useState } from 'react'
import { List, MessageSquareText } from 'lucide-react'
import { Button } from '@/components/ui/button'
import type { DashboardCard } from '@/lib/dashboard'
import { DashboardDataTable } from './DashboardDataTable'

interface DashboardRiskPanelProps {
  cards: DashboardCard[]
  selectedEntityType?: string | null
  selectedEntityId?: string | number | null
  onOpenAll: (card: DashboardCard, trigger: HTMLElement) => void
  onAsk: (card: DashboardCard) => void
  onSelectRow: (card: DashboardCard, row: Record<string, unknown>, trigger: HTMLElement) => void
}

const RISK_LABELS: Record<string, string> = {
  top_rejected_suppliers: '공급업체 반려수량',
  top_scrapped_work_orders: '폐기 작업지시',
}

export function DashboardRiskPanel({
  cards,
  selectedEntityType,
  selectedEntityId,
  onOpenAll,
  onAsk,
  onSelectRow,
}: DashboardRiskPanelProps) {
  const [activeKey, setActiveKey] = useState(cards[0]?.key ?? '')
  const activeCard = cards.find((card) => card.key === activeKey) ?? cards[0]

  if (!activeCard) return null

  return (
    <section className="flex min-h-[360px] min-w-0 flex-col overflow-hidden rounded-[5px] border border-border bg-panel">
      <div className="border-b border-border px-4 py-3.5">
        <div className="flex items-center justify-between gap-3">
          <h2 className="text-[15px] font-semibold text-text">운영 리스크</h2>
          <span className="text-[11px] text-text-faint">
            총 {activeCard.total.toLocaleString()}건
          </span>
        </div>
      </div>
      <div className="flex border-b border-border" role="tablist" aria-label="운영 리스크 종류">
        {cards.map((card) => {
          const selected = card.key === activeCard.key
          return (
            <button
              key={card.key}
              type="button"
              role="tab"
              aria-selected={selected}
              onClick={() => setActiveKey(card.key)}
              className={`relative min-w-0 flex-1 px-3 py-3 text-[12px] font-semibold transition-colors ${
                selected ? 'text-info' : 'text-text-muted hover:bg-panel-2 hover:text-text'
              }`}
            >
              <span className="truncate">{RISK_LABELS[card.key] ?? card.title}</span>
              {selected ? <span className="absolute inset-x-0 bottom-0 h-0.5 bg-info" /> : null}
            </button>
          )
        })}
      </div>
      <div className="min-h-0 flex-1">
        {activeCard.status === 'error' ? (
          <div className="flex h-full min-h-44 items-center justify-center px-4 text-center text-[12px] text-fail">
            이 정보를 불러오지 못했습니다.
          </div>
        ) : (
          <DashboardDataTable
            compact
            columns={activeCard.columns}
            rows={activeCard.rows}
            entityIdField={activeCard.entityIdField}
            selectedId={selectedEntityType === activeCard.entityType ? selectedEntityId : undefined}
            onRowSelect={
              activeCard.entityType
                ? (row, trigger) => onSelectRow(activeCard, row, trigger)
                : undefined
            }
            barColumn={
              activeCard.key === 'top_rejected_suppliers' ? 'totalRejectedQty' : 'scrappedQty'
            }
            barTone={activeCard.key === 'top_rejected_suppliers' ? 'warn' : 'fail'}
            responsiveHiddenColumns={
              activeCard.key === 'top_scrapped_work_orders'
                ? ['productId', 'scrapReasonId']
                : ['supplierId']
            }
          />
        )}
      </div>
      <div className="flex items-center border-t border-border px-2 py-1.5">
        <Button
          type="button"
          variant="ghost"
          size="sm"
          className="flex-1 justify-center text-info"
          onClick={(event) => onOpenAll(activeCard, event.currentTarget)}
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
          onClick={() => onAsk(activeCard)}
        >
          <MessageSquareText />
          AI에게 질문
        </Button>
      </div>
    </section>
  )
}
