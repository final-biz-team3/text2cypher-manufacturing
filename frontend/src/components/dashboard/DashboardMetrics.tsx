import type { LucideIcon } from 'lucide-react'
import { AlertTriangle, Boxes, ClipboardList, PackageSearch, Store, Trash2 } from 'lucide-react'
import type { DashboardKpi } from '@/lib/dashboard'

const KPI_ICONS: Record<string, LucideIcon> = {
  product_count: Boxes,
  active_supplier_count: Store,
  purchased_product_count: PackageSearch,
  low_stock_product_count: AlertTriangle,
  work_order_count: ClipboardList,
  scrapped_work_order_count: Trash2,
}

const PRIORITY_TONES: Record<string, { border: string; icon: string; value: string }> = {
  low_stock_product_count: {
    border: 'border-warn/55',
    icon: 'text-warn',
    value: 'text-text',
  },
  scrapped_work_order_count: {
    border: 'border-fail/45',
    icon: 'text-fail',
    value: 'text-text',
  },
}

function KpiValue({ kpi, className = '' }: { kpi: DashboardKpi; className?: string }) {
  if (kpi.status !== 'ready' || kpi.value === null) {
    return <p className="mt-3 text-[12px] font-medium text-fail">불러오기 실패</p>
  }

  return (
    <p className={`mt-3 font-bold tracking-tight tabular-nums ${className}`}>
      {kpi.value.toLocaleString()}
      <span className="ml-1.5 text-[11px] font-medium tracking-normal text-text-muted">
        {kpi.unit}
      </span>
    </p>
  )
}

export function PriorityMetrics({ kpis }: { kpis: DashboardKpi[] }) {
  return (
    <section aria-labelledby="priority-metrics-title" className="min-w-0">
      <h2 id="priority-metrics-title" className="mb-3 text-[15px] font-semibold text-text">
        주의가 필요한 항목
      </h2>
      <div className="grid grid-cols-1 gap-2.5 sm:grid-cols-2">
        {kpis.map((kpi) => {
          const Icon = KPI_ICONS[kpi.key] ?? AlertTriangle
          const tone = PRIORITY_TONES[kpi.key] ?? PRIORITY_TONES.low_stock_product_count
          return (
            <article
              key={kpi.key}
              className={`min-w-0 rounded-[5px] border bg-panel px-4 py-4 ${tone.border}`}
            >
              <div className="flex items-center gap-2.5">
                <Icon className={`size-4 shrink-0 ${tone.icon}`} aria-hidden="true" />
                <p className="truncate text-[12.5px] font-medium text-text-muted">{kpi.label}</p>
              </div>
              <KpiValue kpi={kpi} className={`text-[30px] ${tone.value}`} />
            </article>
          )
        })}
      </div>
    </section>
  )
}

export function SecondaryMetrics({ kpis }: { kpis: DashboardKpi[] }) {
  const dividerClasses = [
    '',
    'border-t border-border sm:border-t-0 sm:border-l',
    'border-t border-border xl:border-t-0 xl:border-l',
    'border-t border-border sm:border-l xl:border-t-0',
  ]

  return (
    <section aria-labelledby="secondary-metrics-title" className="min-w-0">
      <h2 id="secondary-metrics-title" className="mb-3 text-[15px] font-semibold text-text">
        기본 현황
      </h2>
      <div className="grid overflow-hidden rounded-[5px] border border-border bg-panel sm:grid-cols-2 xl:grid-cols-4">
        {kpis.map((kpi, index) => {
          const Icon = KPI_ICONS[kpi.key] ?? Boxes
          return (
            <article
              key={kpi.key}
              className={`min-w-0 px-4 py-4 ${dividerClasses[index] ?? 'border-t border-border'}`}
            >
              <div className="flex items-center justify-between gap-2">
                <p className="truncate text-[12px] font-medium text-text-muted">{kpi.label}</p>
                <Icon className="size-4 shrink-0 text-info" aria-hidden="true" />
              </div>
              <KpiValue kpi={kpi} className="text-[27px] text-text" />
            </article>
          )
        })}
      </div>
    </section>
  )
}
