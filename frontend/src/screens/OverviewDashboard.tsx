import { useEffect, useRef, useState } from 'react'
import {
  AlertTriangle,
  ArrowRight,
  Boxes,
  ClipboardList,
  Factory,
  MessageSquareText,
  PackageSearch,
  RefreshCw,
  Store,
  Trash2,
} from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import { TopBar } from '@/components/layout/TopBar'
import { AnalysisCard } from '@/components/dashboard/AnalysisCard'
import { DashboardDrawer } from '@/components/dashboard/DashboardDrawer'
import { formatSnapshotDateTime } from '@/components/dashboard/dashboardFormatters'
import { Button } from '@/components/ui/button'
import { fetchDashboardOverview, type DashboardKpi } from '@/lib/dashboard'
import { useAuthStore } from '@/store/useAuthStore'
import { useDashboardStore } from '@/store/useDashboardStore'
import { useHealthStore } from '@/store/useHealthStore'

const KPI_ICONS = {
  product_count: Boxes,
  active_supplier_count: Store,
  purchased_product_count: PackageSearch,
  low_stock_product_count: AlertTriangle,
  work_order_count: ClipboardList,
  scrapped_work_order_count: Trash2,
} as const

const KPI_TONES: Record<string, string> = {
  low_stock_product_count: 'text-warn',
  scrapped_work_order_count: 'text-fail',
  active_supplier_count: 'text-success',
}

const CARD_QUESTIONS: Record<string, string> = {
  low_stock_top5: '안전재고가 부족한 제품과 부족 수량을 알려줘.',
  top_finished_sales: '판매 주문 수량이 많은 완제품을 알려줘.',
  top_rejected_suppliers: '구매주문 반려수량이 많은 공급업체를 알려줘.',
  top_scrapped_work_orders: '폐기수량이 많은 작업지시와 폐기사유를 알려줘.',
  busiest_locations: '작업지시를 가장 많이 처리한 작업장을 알려줘.',
  category_price_summary: '제품 분류별 제품 수와 평균 정가를 알려줘.',
  top_suppliers_by_product_count: '공급 제품 종류가 많은 활성 공급업체를 알려줘.',
}

function KpiCard({ kpi }: { kpi: DashboardKpi }) {
  const Icon = KPI_ICONS[kpi.key as keyof typeof KPI_ICONS] ?? Factory
  return (
    <section className="min-w-0 rounded-md border border-border bg-panel px-4 py-4 shadow-[0_1px_2px_rgba(15,23,42,0.03)]">
      <div className="flex items-center justify-between gap-2">
        <p className="truncate text-[11.5px] font-medium text-text-muted">{kpi.label}</p>
        <Icon
          className={`size-4 shrink-0 ${KPI_TONES[kpi.key] ?? 'text-info'}`}
          aria-hidden="true"
        />
      </div>
      {kpi.status === 'ready' && kpi.value !== null ? (
        <p className="mt-4 text-[27px] font-bold tracking-tight text-text tabular-nums">
          {kpi.value.toLocaleString()}
          <span className="ml-1 text-[11px] font-medium tracking-normal text-text-muted">
            {kpi.unit}
          </span>
        </p>
      ) : (
        <p className="mt-4 text-[12px] font-medium text-fail">불러오기 실패</p>
      )}
    </section>
  )
}

function DashboardSkeleton() {
  return (
    <div className="animate-pulse" aria-label="대시보드 불러오는 중">
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6">
        {Array.from({ length: 6 }, (_, index) => (
          <div key={index} className="h-28 rounded-md border border-border bg-panel" />
        ))}
      </div>
      <div className="mt-5 grid grid-cols-1 gap-4 xl:grid-cols-2">
        {Array.from({ length: 4 }, (_, index) => (
          <div key={index} className="h-72 rounded-md border border-border bg-panel" />
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

  return (
    <div className="flex min-h-screen flex-col bg-bg">
      <TopBar
        connected={neo4jConnected}
        postgresConnected={postgresConnected}
        readOnly
        onNavigateHome={() => navigate('/dashboard')}
        activeSection="dashboard"
        onNavigateDashboard={() => window.scrollTo({ top: 0, behavior: 'smooth' })}
        onNavigateChat={() => goToChat()}
        snapshotLabel={overview?.snapshot.label}
        username={user?.username}
        onLogout={logout}
      />
      <main className="mx-auto w-full max-w-[1600px] flex-1 px-4 py-5 sm:px-6 lg:px-8">
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
                    BOM 기준 {overview.snapshot.bomAsOfDate}
                  </span>
                </>
              ) : null}
            </div>
            <p className="mt-1 text-[11.5px] text-text-muted">
              {overview
                ? `${overview.snapshot.scope}로 제품·재고·공급업체·작업지시 정보를 조회합니다.`
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
            <section
              aria-label="핵심 지표"
              className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6"
            >
              {overview.kpis.map((kpi) => (
                <KpiCard key={kpi.key} kpi={kpi} />
              ))}
            </section>
            {overview.errors.length > 0 ? (
              <p role="status" className="mt-3 text-[11px] text-warn">
                일부 항목을 불러오지 못했습니다. 표시된 다른 항목은 계속 사용할 수 있습니다.
              </p>
            ) : null}
            <section aria-label="분석 목록" className="mt-5 grid grid-cols-1 gap-4 xl:grid-cols-2">
              {overview.cards.map((card) => (
                <AnalysisCard
                  key={card.key}
                  card={card}
                  selectedEntityId={
                    selectedEntity && selectedEntity.type === card.entityType
                      ? selectedEntity.id
                      : null
                  }
                  onOpenAll={(trigger) => {
                    returnFocusRef.current = trigger
                    setSelectedCardKey(card.key)
                  }}
                  onAsk={() => goToChat(CARD_QUESTIONS[card.key] ?? `${card.title} 정보를 알려줘.`)}
                  onSelectRow={(row, trigger) => {
                    const id = card.entityIdField ? row[card.entityIdField] : null
                    if (!card.entityType || (typeof id !== 'string' && typeof id !== 'number'))
                      return
                    returnFocusRef.current = trigger
                    setSelectedCardKey(card.key)
                    setSelectedEntity({ type: card.entityType, id })
                  }}
                />
              ))}
            </section>
          </>
        ) : null}
      </main>
      <DashboardDrawer onAsk={goToChat} returnFocusRef={returnFocusRef} />
    </div>
  )
}
