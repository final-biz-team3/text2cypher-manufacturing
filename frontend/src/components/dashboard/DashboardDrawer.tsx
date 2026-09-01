import { useEffect, useRef, useState } from 'react'
import { ArrowLeft, ChevronLeft, ChevronRight, Loader2, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { DashboardDataTable } from './DashboardDataTable'
import { EntityDetailContent } from './EntityDetailContent'
import {
  fetchDashboardCard,
  fetchEntityDetail,
  type DashboardCard,
  type EntityDetail,
} from '@/lib/dashboard'
import { useDashboardStore, type EntitySelection } from '@/store/useDashboardStore'

interface DashboardDrawerProps {
  onAsk: (question: string) => void
  returnFocusRef: React.MutableRefObject<HTMLElement | null>
}

export function DashboardDrawer({ onAsk, returnFocusRef }: DashboardDrawerProps) {
  const selectedCardKey = useDashboardStore((state) => state.selectedCardKey)
  const selectedEntity = useDashboardStore((state) => state.selectedEntity)
  const setSelectedEntity = useDashboardStore((state) => state.setSelectedEntity)
  const resetPanels = useDashboardStore((state) => state.resetPanels)
  const [card, setCard] = useState<DashboardCard | null>(null)
  const [page, setPage] = useState(1)
  const [sort, setSort] = useState<string | undefined>()
  const [direction, setDirection] = useState<'asc' | 'desc'>('desc')
  const [cardErrorKey, setCardErrorKey] = useState<string | null>(null)
  const [detailState, setDetailState] = useState<{
    key: string
    detail: EntityDetail | null
    error: string | null
  }>({ key: '', detail: null, error: null })
  const closeButtonRef = useRef<HTMLButtonElement>(null)

  const open = selectedCardKey !== null || selectedEntity !== null

  useEffect(() => {
    if (!selectedCardKey) return
    const controller = new AbortController()
    fetchDashboardCard(selectedCardKey, { page, pageSize: 20, sort, direction }, controller.signal)
      .then((nextCard) => {
        setCard(nextCard)
        setCardErrorKey(null)
      })
      .catch((error: unknown) => {
        if (!controller.signal.aborted) {
          console.error('fetchDashboardCard failed:', error)
          setCardErrorKey(selectedCardKey)
        }
      })
    return () => controller.abort()
  }, [direction, page, selectedCardKey, sort])

  useEffect(() => {
    if (!selectedEntity) return
    const controller = new AbortController()
    const requestKey = `${selectedEntity.type}:${String(selectedEntity.id)}`
    fetchEntityDetail(selectedEntity.type, selectedEntity.id, controller.signal)
      .then((nextDetail) => setDetailState({ key: requestKey, detail: nextDetail, error: null }))
      .catch((error: unknown) => {
        if (!controller.signal.aborted) {
          console.error('fetchEntityDetail failed:', error)
          setDetailState({
            key: requestKey,
            detail: null,
            error: '상세 정보를 불러오지 못했습니다. 기존 선택 정보는 유지됩니다.',
          })
        }
      })
    return () => controller.abort()
  }, [selectedEntity])

  useEffect(() => {
    if (!open) return
    closeButtonRef.current?.focus()
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        resetPanels()
        window.setTimeout(() => returnFocusRef.current?.focus(), 0)
      }
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [open, resetPanels, returnFocusRef])

  if (!open) return null

  const selectedEntityKey = selectedEntity
    ? `${selectedEntity.type}:${String(selectedEntity.id)}`
    : ''
  const detail = detailState.key === selectedEntityKey ? detailState.detail : null
  const detailError = detailState.key === selectedEntityKey ? detailState.error : null
  const cardLoading = Boolean(
    selectedCardKey &&
    (!card ||
      card.key !== selectedCardKey ||
      card.page !== page ||
      card.direction !== direction ||
      (sort !== undefined && card.sort !== sort)),
  )

  const close = () => {
    resetPanels()
    setPage(1)
    setSort(undefined)
    setDirection('desc')
    window.setTimeout(() => returnFocusRef.current?.focus(), 0)
  }
  const openRelated = (entity: EntitySelection, trigger: HTMLElement) => {
    returnFocusRef.current = trigger
    setSelectedEntity(entity)
  }
  const handleSort = (column: string) => {
    if (sort === column) setDirection((current) => (current === 'desc' ? 'asc' : 'desc'))
    else {
      setSort(column)
      setDirection('desc')
    }
    setPage(1)
  }

  return (
    <aside
      aria-label={selectedEntity ? '엔티티 상세' : '카드 전체 목록'}
      className="fixed inset-y-0 right-0 z-50 flex w-full flex-col border-l border-border bg-panel shadow-[-12px_0_32px_rgba(15,23,42,0.12)] sm:w-[480px]"
    >
      <div className="flex h-14 shrink-0 items-center justify-between border-b border-border px-3">
        <Button
          ref={closeButtonRef}
          type="button"
          variant="ghost"
          size="sm"
          onClick={() => (selectedEntity && selectedCardKey ? setSelectedEntity(null) : close())}
        >
          <ArrowLeft />
          {selectedEntity && selectedCardKey ? '목록으로' : '대시보드로'}
        </Button>
        <Button type="button" variant="ghost" size="icon-sm" onClick={close} aria-label="패널 닫기">
          <X />
        </Button>
      </div>
      {selectedEntity ? (
        <EntityDetailContent
          detail={detail}
          loading={!detail && !detailError}
          error={detailError}
          onAsk={onAsk}
          onSelectEntity={openRelated}
        />
      ) : cardLoading ? (
        <div className="flex flex-1 items-center justify-center gap-2 text-[12px] text-text-muted">
          <Loader2 className="size-4 animate-spin" /> 전체 목록을 불러오는 중입니다…
        </div>
      ) : card && cardErrorKey !== selectedCardKey ? (
        <div className="flex min-h-0 flex-1 flex-col">
          <div className="border-b border-border px-5 py-4">
            <h2 className="text-[17px] font-bold text-text">{card.title}</h2>
            <p className="mt-1 text-[11px] text-text-muted">전체 {card.total.toLocaleString()}건</p>
          </div>
          <div className="min-h-0 flex-1 overflow-auto">
            <DashboardDataTable
              columns={card.columns}
              rows={card.rows}
              entityIdField={card.entityIdField}
              onRowSelect={
                card.entityType
                  ? (row, trigger) => {
                      const id = card.entityIdField ? row[card.entityIdField] : null
                      if (typeof id === 'string' || typeof id === 'number')
                        openRelated({ type: card.entityType!, id }, trigger)
                    }
                  : undefined
              }
              onSort={handleSort}
              sortableColumns={card.sortableColumns}
              sort={card.sort}
              direction={card.direction}
            />
          </div>
          <div className="flex items-center justify-between border-t border-border px-4 py-3">
            <p className="text-[10.5px] text-text-muted">
              {card.page} / {Math.max(1, Math.ceil(card.total / (card.pageSize ?? 20)))} 페이지
            </p>
            <div className="flex gap-1">
              <Button
                type="button"
                variant="outline"
                size="icon-sm"
                disabled={page <= 1 || cardLoading}
                onClick={() => setPage((value) => value - 1)}
                aria-label="이전 페이지"
              >
                <ChevronLeft />
              </Button>
              <Button
                type="button"
                variant="outline"
                size="icon-sm"
                disabled={page * (card.pageSize ?? 20) >= card.total || cardLoading}
                onClick={() => setPage((value) => value + 1)}
                aria-label="다음 페이지"
              >
                <ChevronRight />
              </Button>
            </div>
          </div>
        </div>
      ) : (
        <div className="flex flex-1 items-center justify-center text-[12px] text-fail">
          전체 목록을 불러오지 못했습니다.
        </div>
      )}
    </aside>
  )
}
