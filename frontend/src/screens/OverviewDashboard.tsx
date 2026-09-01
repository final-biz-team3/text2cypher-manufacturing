import { useEffect, useRef, useState } from 'react'
import { AlertTriangle, ArrowRight, MessageSquareText, RefreshCw } from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import { TopBar } from '@/components/layout/TopBar'
import { AppSidebar } from '@/components/layout/AppSidebar'
import { AnalysisCard } from '@/components/dashboard/AnalysisCard'
import { DashboardRiskPanel } from '@/components/dashboard/DashboardRiskPanel'
import { PriorityMetrics, SecondaryMetrics } from '@/components/dashboard/DashboardMetrics'
import { DashboardDrawer } from '@/components/dashboard/DashboardDrawer'
import { ProcessOverviewSection } from '@/components/dashboard/ProcessOverviewSection'
import { formatSnapshotDateTime } from '@/components/dashboard/dashboardFormatters'
import { Button } from '@/components/ui/button'
import { fetchDashboardOverview, type DashboardCard } from '@/lib/dashboard'
import { useAuthStore } from '@/store/useAuthStore'
import { useDashboardStore } from '@/store/useDashboardStore'
import { useHealthStore } from '@/store/useHealthStore'

const PRIORITY_KPI_KEYS = ['low_stock_product_count', 'scrapped_work_order_count']
const SECONDARY_KPI_KEYS = [
  'product_count',
  'active_supplier_count',
  'purchased_product_count',
  'work_order_count',
]

const CARD_QUESTIONS: Record<string, string> = {
  low_stock_top5: '안전재고가 부족한 제품과 부족 수량을 알려줘.',
  top_finished_sales: '판매 주문 수량이 많은 완제품을 알려줘.',
  top_rejected_suppliers: '구매주문 반려수량이 많은 공급업체를 알려줘.',
  top_scrapped_work_orders: '폐기수량이 많은 작업지시와 폐기사유를 알려줘.',
  busiest_locations: '작업지시를 가장 많이 처리한 작업장을 알려줘.',
  category_price_summary: '제품 분류별 제품 수와 평균 정가를 알려줘.',
  top_suppliers_by_product_count: '공급 제품 종류가 많은 활성 공급업체를 알려줘.',
}

function orderByKeys<T extends { key: string }>(items: T[], keys: string[]): T[] {
  const itemMap = new Map(items.map((item) => [item.key, item]))
  return keys.flatMap((key) => {
    const item = itemMap.get(key)
    return item ? [item] : []
  })
}

function DashboardSkeleton() {
  return (
    <div className="animate-pulse" aria-label="대시보드 불러오는 중">
      <div className="grid gap-5 xl:grid-cols-[minmax(360px,0.8fr)_minmax(0,1.6fr)]">
        <div className="h-32 rounded-[5px] border border-border bg-panel" />
        <div className="h-32 rounded-[5px] border border-border bg-panel" />
      </div>
      <div className="mt-4 grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,1.25fr)_minmax(380px,0.9fr)]">
        {Array.from({ length: 2 }, (_, index) => (
          <div key={index} className="h-[360px] rounded-[5px] border border-border bg-panel" />
        ))}
      </div>
    </div>
  )
}

export function OverviewDashboard() {
  const navigate = useNavigate()
  const user = useAuthStore((state) => state.user)
  const logout = useAuthStore((state) => state.logout)
  const neo4jConnected = useHealthStore((state) => state.neo4jConnected)
  const postgresConnected = useHealthStore((state) => state.postgresConnected)
  const overview = useDashboardStore((state) => state.overview)
  const setOverview = useDashboardStore((state) => state.setOverview)
  const selectedEntity = useDashboardStore((state) => state.selectedEntity)
  const setSelectedEntity = useDashboardStore((state) => state.setSelectedEntity)
  const setSelectedCardKey = useDashboardStore((state) => state.setSelectedCardKey)
  const resetPanels = useDashboardStore((state) => state.resetPanels)
  const [loading, setLoading] = useState(!overview)
  const [error, setError] = useState<string | null>(null)
  const returnFocusRef = useRef<HTMLElement | null>(null)

  const loadOverview = () => {
    const controller = new AbortController()
    setLoading(true)
    setError(null)
    fetchDashboardOverview(controller.signal)
      .then(setOverview)
      .catch((fetchError: unknown) => {
        if (!controller.signal.aborted) {
          console.error('fetchDashboardOverview failed:', fetchError)
          setError('전체 현황을 불러오지 못했습니다. DB 연결 상태를 확인해 주세요.')
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false)
      })
    return controller
  }

  useEffect(() => {
    const controller = new AbortController()
    fetchDashboardOverview(controller.signal)
      .then(setOverview)
      .catch((fetchError: unknown) => {
        if (!controller.signal.aborted) {
          console.error('fetchDashboardOverview failed:', fetchError)
          setError('전체 현황을 불러오지 못했습니다. DB 연결 상태를 확인해 주세요.')
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false)
      })
    return () => controller.abort()
  }, [setOverview])

  useEffect(() => () => resetPanels(), [resetPanels])

  const goToChat = (draftQuestion?: string) => {
    navigate('/chat', draftQuestion ? { state: { draftQuestion } } : undefined)
  }

  const openCard = (card: DashboardCard, trigger: HTMLElement) => {
    returnFocusRef.current = trigger
    setSelectedCardKey(card.key)
  }

  const askCard = (card: DashboardCard) => {
    goToChat(CARD_QUESTIONS[card.key] ?? `${card.title} 정보를 알려줘.`)
  }

  const selectCardRow = (
    card: DashboardCard,
    row: Record<string, unknown>,
    trigger: HTMLElement,
  ) => {
    const id = card.entityIdField ? row[card.entityIdField] : null
    if (!card.entityType || (typeof id !== 'string' && typeof id !== 'number')) return
    returnFocusRef.current = trigger
    setSelectedCardKey(card.key)
    setSelectedEntity({ type: card.entityType, id })
  }

  const priorityKpis = overview ? orderByKeys(overview.kpis, PRIORITY_KPI_KEYS) : []
  const secondaryKpis = overview ? orderByKeys(overview.kpis, SECONDARY_KPI_KEYS) : []
  const primaryCard = overview ? orderByKeys(overview.cards, ['low_stock_top5'])[0] : undefined
  const riskCards = overview
    ? orderByKeys(overview.cards, ['top_rejected_suppliers', 'top_scrapped_work_orders'])
    : []
  const comparisonCards = overview
    ? orderByKeys(overview.cards, ['top_finished_sales', 'busiest_locations'])
    : []
  const supportingCards = overview
    ? orderByKeys(overview.cards, ['category_price_summary', 'top_suppliers_by_product_count'])
    : []

  return (
    <div className="flex h-screen flex-col overflow-hidden bg-bg">
      <TopBar
        connected={neo4jConnected}
        postgresConnected={postgresConnected}
        readOnly
        onNavigateHome={() => goToChat()}
        snapshotLabel={overview?.snapshot.label}
        username={user?.username}
        onLogout={logout}
      />
      <div className="flex min-h-0 flex-1">
        <AppSidebar
          activeSection="dashboard"
          onNavigateDashboard={() => window.scrollTo({ top: 0, behavior: 'smooth' })}
          onNavigateChat={() => goToChat()}
        />
        <main className="min-w-0 flex-1 overflow-x-hidden overflow-y-auto px-4 py-5 sm:px-6 lg:px-8">
          <div className="mx-auto w-full max-w-[1600px]">
            <div className="mb-5 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
              <div>
                <div className="flex flex-wrap items-center gap-2">
                  <h1 className="text-[22px] font-bold tracking-tight text-text">전체 현황</h1>
                  {overview ? (
                    <>
                      <span className="rounded-sm border border-border bg-panel px-2 py-1 text-[10px] text-text-muted">
                        데이터 동기화 {formatSnapshotDateTime(overview.snapshot.syncedAt)}
                      </span>
                      <span className="rounded-sm border border-border bg-panel px-2 py-1 text-[10px] text-text-muted">
                        BOM 관계 유효 기준 {overview.snapshot.bomAsOfDate}
                      </span>
                    </>
                  ) : null}
                </div>
                <p className="mt-1 text-[11.5px] text-text-muted">
                  {overview
                    ? `집계 범위: ${overview.snapshot.scope}. 제품·재고·공급업체 정보는 전체 적재 데이터를 사용합니다.`
                    : 'AdventureWorks 전체 데이터 스냅샷을 불러오는 중입니다.'}
                </p>
              </div>
              <div className="flex items-center gap-2">
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={() => loadOverview()}
                  disabled={loading}
                >
                  <RefreshCw className={loading ? 'animate-spin' : ''} />
                  새로고침
                </Button>
                <Button type="button" size="lg" onClick={() => goToChat()}>
                  <MessageSquareText />
                  AI Chat으로 이동
                  <ArrowRight />
                </Button>
              </div>
            </div>

            {error && !overview ? (
              <div
                role="alert"
                className="flex min-h-64 flex-col items-center justify-center gap-3 rounded-md border border-fail/30 bg-panel px-5 text-center"
              >
                <AlertTriangle className="size-6 text-fail" />
                <p className="text-[13px] font-medium text-fail">{error}</p>
                <Button type="button" variant="outline" onClick={() => loadOverview()}>
                  다시 시도
                </Button>
              </div>
            ) : loading && !overview ? (
              <DashboardSkeleton />
            ) : overview ? (
              <>
                <section className="grid gap-5 border-t border-border pt-4 xl:grid-cols-[minmax(360px,0.8fr)_minmax(0,1.6fr)]">
                  <PriorityMetrics kpis={priorityKpis} />
                  <SecondaryMetrics kpis={secondaryKpis} />
                </section>
                {overview.errors.length > 0 ? (
                  <p role="status" className="mt-3 text-[12px] text-warn">
                    일부 항목을 불러오지 못했습니다. 표시된 다른 항목은 계속 사용할 수 있습니다.
                  </p>
                ) : null}
                <ProcessOverviewSection />
                <div className="mt-5 flex flex-wrap items-center gap-2 border-t border-border pt-5">
                  <h2 className="text-[15px] font-semibold text-text">전체 데이터 분석</h2>
                  <span className="rounded-sm border border-border bg-panel px-2 py-1 text-[10px] text-text-muted">
                    전체 적재 데이터
                  </span>
                </div>
                <section className="mt-4 grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,1.25fr)_minmax(380px,0.9fr)]">
                  {primaryCard ? (
                    <AnalysisCard
                      card={primaryCard}
                      selectedEntityId={
                        selectedEntity && selectedEntity.type === primaryCard.entityType
                          ? selectedEntity.id
                          : null
                      }
                      onOpenAll={(trigger) => openCard(primaryCard, trigger)}
                      onAsk={() => askCard(primaryCard)}
                      onSelectRow={(row, trigger) => selectCardRow(primaryCard, row, trigger)}
                      barColumn="shortageQty"
                      barTone="warn"
                      minHeightClassName="min-h-[360px]"
                      responsiveHiddenColumns={['productId', 'productNumber']}
                    />
                  ) : null}
                  <DashboardRiskPanel
                    cards={riskCards}
                    selectedEntityType={selectedEntity?.type}
                    selectedEntityId={selectedEntity?.id}
                    onOpenAll={openCard}
                    onAsk={askCard}
                    onSelectRow={selectCardRow}
                  />
                </section>
                <section
                  aria-label="성과와 작업장 비교"
                  className="mt-4 grid grid-cols-1 gap-4 xl:grid-cols-2"
                >
                  {comparisonCards.map((card) => (
                    <AnalysisCard
                      key={card.key}
                      card={card}
                      selectedEntityId={
                        selectedEntity && selectedEntity.type === card.entityType
                          ? selectedEntity.id
                          : null
                      }
                      onOpenAll={(trigger) => openCard(card, trigger)}
                      onAsk={() => askCard(card)}
                      onSelectRow={(row, trigger) => selectCardRow(card, row, trigger)}
                      barColumn={
                        card.key === 'top_finished_sales' ? 'totalOrderQty' : 'workOrderCount'
                      }
                      minHeightClassName="min-h-[310px]"
                      displayTitle={card.key === 'busiest_locations' ? '작업장 현황' : undefined}
                      responsiveHiddenColumns={
                        card.key === 'top_finished_sales'
                          ? ['productId', 'productNumber']
                          : ['operationCount']
                      }
                    />
                  ))}
                </section>
                <section
                  aria-label="기준 정보 분석"
                  className="mt-4 grid grid-cols-1 gap-4 pb-6 xl:grid-cols-2"
                >
                  {supportingCards.map((card) => (
                    <AnalysisCard
                      key={card.key}
                      card={card}
                      selectedEntityId={
                        selectedEntity && selectedEntity.type === card.entityType
                          ? selectedEntity.id
                          : null
                      }
                      onOpenAll={(trigger) => openCard(card, trigger)}
                      onAsk={() => askCard(card)}
                      onSelectRow={(row, trigger) => selectCardRow(card, row, trigger)}
                      barColumn={
                        card.key === 'top_suppliers_by_product_count'
                          ? 'suppliedProductCount'
                          : undefined
                      }
                      minHeightClassName="min-h-[300px]"
                    />
                  ))}
                </section>
              </>
            ) : null}
          </div>
        </main>
      </div>
      <DashboardDrawer onAsk={goToChat} returnFocusRef={returnFocusRef} />
    </div>
  )
}
