import { useEffect, useMemo, useRef, useState } from 'react'
import {
  AlertTriangle,
  CalendarDays,
  CheckCircle2,
  Factory,
  LoaderCircle,
  PlayCircle,
  RotateCcw,
  Trash2,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import {
  fetchProcessOverview,
  getProcessGranularityOptions,
  resolveProcessGranularity,
  type DashboardKpi,
  type ProcessGranularity,
  type ProcessOverview,
} from '@/lib/dashboard'

const CHART_WIDTH = 720
const CHART_HEIGHT = 236
const CHART_PADDING = { top: 24, right: 18, bottom: 38, left: 54 }
const AXIS_INTERVALS = 4

const KPI_ICONS = {
  startedWorkOrderCount: PlayCircle,
  completedWorkOrderCount: CheckCircle2,
  operationCount: Factory,
  scrappedWorkOrderCount: AlertTriangle,
  scrappedQty: Trash2,
}

type Preset = '7d' | '30d' | '90d' | 'all'

function GranularityToggle({
  label,
  value,
  options,
  disabled,
  onChange,
}: {
  label: string
  value: ProcessGranularity
  options: ProcessGranularity[]
  disabled: boolean
  onChange: (granularity: ProcessGranularity) => void
}) {
  return (
    <div
      className="flex rounded-[4px] border border-border bg-panel-2 p-0.5"
      role="group"
      aria-label={label}
    >
      {options.map((granularity) => (
        <button
          key={granularity}
          type="button"
          onClick={() => onChange(granularity)}
          disabled={disabled}
          aria-pressed={value === granularity}
          className="min-w-8 rounded-[3px] px-2 py-1.5 text-[10.5px] font-medium text-text-muted transition-colors hover:text-text aria-pressed:bg-accent-bg aria-pressed:text-info disabled:opacity-50"
        >
          {{ day: '일', month: '월', year: '년' }[granularity]}
        </button>
      ))}
    </div>
  )
}

function addDays(value: string, days: number): string {
  const next = new Date(`${value}T00:00:00Z`)
  next.setUTCDate(next.getUTCDate() + days)
  return next.toISOString().slice(0, 10)
}

function granularityLabel(granularity: ProcessGranularity): string {
  return { day: '일별', month: '월별', year: '연도별' }[granularity]
}

function formatPeriodDate(value: string, granularity: ProcessGranularity): string {
  if (granularity === 'year') return value.slice(0, 4)
  if (granularity === 'month') return value.slice(0, 7).replace('-', '.')
  const date = new Date(`${value}T00:00:00Z`)
  return new Intl.DateTimeFormat('ko-KR', {
    month: 'numeric',
    day: 'numeric',
    timeZone: 'UTC',
  }).format(date)
}

function tickIndexes(length: number): number[] {
  if (length <= 1) return [0]
  return Array.from(new Set([0, 1, 2, 3, 4].map((index) => Math.round((index * (length - 1)) / 4))))
}

function getAxisScale(value: number): { max: number; ticks: number[] } {
  const target = Math.max(1, value)
  const roughStep = target / AXIS_INTERVALS
  const magnitude = 10 ** Math.floor(Math.log10(roughStep))
  const normalized = roughStep / magnitude
  const step = (normalized < 2 ? 1 : normalized < 2.5 ? 2 : normalized < 5 ? 2.5 : 5) * magnitude
  const max = Math.ceil(target / step) * step
  const intervalCount = Math.round(max / step)
  return {
    max,
    ticks: Array.from({ length: intervalCount + 1 }, (_, index) => max - index * step),
  }
}

function ChartTooltip({
  left,
  date,
  granularity,
  values,
}: {
  left: number
  date: string
  granularity: ProcessGranularity
  values: { label: string; value: number; unit: string; color: string }[]
}) {
  return (
    <div
      className="pointer-events-none absolute top-1 z-10 min-w-36 -translate-x-1/2 rounded-[4px] border border-border bg-panel/95 px-3 py-2 shadow-lg backdrop-blur-sm"
      style={{ left: `${Math.min(86, Math.max(14, left))}%` }}
      role="status"
    >
      <p className="text-[10.5px] font-medium text-text">{formatPeriodDate(date, granularity)}</p>
      <div className="mt-1.5 grid gap-1">
        {values.map((item) => (
          <p key={item.label} className="flex items-center justify-between gap-4 text-[10px]">
            <span className="flex items-center gap-1.5 text-text-muted">
              <span className="size-1.5 rounded-full" style={{ backgroundColor: item.color }} />
              {item.label}
            </span>
            <strong className="font-semibold tabular-nums text-text">
              {item.value.toLocaleString()}
              {item.unit}
            </strong>
          </p>
        ))}
      </div>
    </div>
  )
}

function ProcessMetric({ kpi }: { kpi: DashboardKpi }) {
  const Icon = KPI_ICONS[kpi.key as keyof typeof KPI_ICONS] ?? Factory
  const isRisk = kpi.key === 'scrappedWorkOrderCount' || kpi.key === 'scrappedQty'

  return (
    <article className="min-w-0 border-l border-border pl-3 first:border-l-0 first:pl-0 sm:pl-4">
      <div className="flex items-center gap-2 text-text-muted">
        <Icon className={`size-3.5 ${isRisk ? 'text-warn' : 'text-info'}`} aria-hidden="true" />
        <p className="min-w-0 text-[11px] leading-4 font-medium">{kpi.label}</p>
      </div>
      <p className="mt-2 text-[23px] font-bold tracking-tight tabular-nums text-text">
        {kpi.value?.toLocaleString() ?? '—'}
        <span className="ml-1 text-[10px] font-medium text-text-muted">{kpi.unit}</span>
      </p>
    </article>
  )
}

function WorkOrderTrendChart({
  data,
  granularityOptions,
  loading,
  onGranularityChange,
}: {
  data: ProcessOverview
  granularityOptions: ProcessGranularity[]
  loading: boolean
  onGranularityChange: (granularity: ProcessGranularity) => void
}) {
  const [activeIndex, setActiveIndex] = useState<number | null>(null)
  const rows = data.trend
  const plotWidth = CHART_WIDTH - CHART_PADDING.left - CHART_PADDING.right
  const plotHeight = CHART_HEIGHT - CHART_PADDING.top - CHART_PADDING.bottom
  const dataMax = Math.max(
    1,
    ...rows.flatMap((row) => [row.startedWorkOrderCount, row.completedWorkOrderCount]),
  )
  const axis = getAxisScale(dataMax)
  const x = (index: number) =>
    CHART_PADDING.left +
    (rows.length <= 1 ? plotWidth / 2 : (index / (rows.length - 1)) * plotWidth)
  const y = (value: number) => CHART_PADDING.top + plotHeight - (value / axis.max) * plotHeight
  const points = (key: 'startedWorkOrderCount' | 'completedWorkOrderCount') =>
    rows.map((row, index) => `${x(index)},${y(row[key])}`).join(' ')
  const ticks = tickIndexes(rows.length)
  const activeRow = activeIndex === null ? null : rows[activeIndex]
  const activeLeft = activeIndex === null ? 50 : (x(activeIndex) / CHART_WIDTH) * 100

  return (
    <section className="min-w-0 rounded-[5px] border border-border bg-panel p-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h3 className="text-[13px] font-semibold text-text">작업지시 시작·완료 추이</h3>
          <p className="mt-0.5 text-[10.5px] text-text-muted">
            {granularityLabel(data.period.granularity)} 집계
          </p>
        </div>
        <div className="flex flex-wrap items-center justify-end gap-3">
          <div className="flex gap-3 text-[10.5px] text-text-muted" aria-hidden="true">
            <span className="flex items-center gap-1.5">
              <span className="w-3 border-t-2 border-info" /> 시작
            </span>
            <span className="flex items-center gap-1.5">
              <span className="w-3 border-t-2 border-dashed border-success" /> 완료
            </span>
          </div>
          <GranularityToggle
            label="작업지시 추이 집계 기준"
            value={data.period.granularity}
            options={granularityOptions}
            disabled={loading}
            onChange={onGranularityChange}
          />
        </div>
      </div>
      <div className="relative mt-3 aspect-[720/236] w-full overflow-hidden">
        {activeRow ? (
          <ChartTooltip
            left={activeLeft}
            date={activeRow.date}
            granularity={data.period.granularity}
            values={[
              {
                label: '시작',
                value: activeRow.startedWorkOrderCount,
                unit: '건',
                color: 'var(--info)',
              },
              {
                label: '완료',
                value: activeRow.completedWorkOrderCount,
                unit: '건',
                color: 'var(--success)',
              },
            ]}
          />
        ) : null}
        <svg
          className="block size-full"
          viewBox={`0 0 ${CHART_WIDTH} ${CHART_HEIGHT}`}
          role="img"
          aria-label={`선택 기간의 작업지시 시작 및 완료 ${granularityLabel(data.period.granularity)} 추이`}
        >
          <text x="8" y="13" fill="var(--text-muted)" fontSize="11">
            단위: 건
          </text>
          {axis.ticks.map((value, index) => {
            const gridY = CHART_PADDING.top + (plotHeight * index) / (axis.ticks.length - 1)
            return (
              <g key={value}>
                <line
                  x1={CHART_PADDING.left}
                  x2={CHART_WIDTH - CHART_PADDING.right}
                  y1={gridY}
                  y2={gridY}
                  stroke="var(--border)"
                  strokeWidth={value === 0 ? '1.2' : '1'}
                />
                <text
                  x={CHART_PADDING.left - 8}
                  y={gridY + 4}
                  textAnchor="end"
                  fill="var(--text-muted)"
                  fontSize="11"
                >
                  {value.toLocaleString()}
                </text>
              </g>
            )
          })}
          {rows.length > 0 ? (
            <>
              <polyline
                points={points('startedWorkOrderCount')}
                fill="none"
                stroke="var(--info)"
                strokeWidth="2.5"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
              <polyline
                points={points('completedWorkOrderCount')}
                fill="none"
                stroke="var(--success)"
                strokeWidth="2.5"
                strokeDasharray="6 4"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
              {activeIndex !== null ? (
                <line
                  x1={x(activeIndex)}
                  x2={x(activeIndex)}
                  y1={CHART_PADDING.top}
                  y2={CHART_PADDING.top + plotHeight}
                  stroke="var(--text-muted)"
                  strokeWidth="1"
                  strokeDasharray="3 4"
                  opacity="0.65"
                />
              ) : null}
              {rows.map((row, index) => (
                <g key={row.date}>
                  <circle
                    cx={x(index)}
                    cy={y(row.startedWorkOrderCount)}
                    r={activeIndex === index ? '4.5' : '3'}
                    fill="var(--info)"
                    stroke="var(--panel)"
                    strokeWidth="1.5"
                  />
                  <circle
                    cx={x(index)}
                    cy={y(row.completedWorkOrderCount)}
                    r={activeIndex === index ? '4.5' : '3'}
                    fill="var(--success)"
                    stroke="var(--panel)"
                    strokeWidth="1.5"
                  />
                  <rect
                    x={x(index) - Math.max(7, plotWidth / Math.max(rows.length, 1) / 2)}
                    y={CHART_PADDING.top}
                    width={Math.max(14, plotWidth / Math.max(rows.length, 1))}
                    height={plotHeight}
                    fill="transparent"
                    tabIndex={0}
                    aria-label={`${row.date}, 시작 ${row.startedWorkOrderCount.toLocaleString()}건, 완료 ${row.completedWorkOrderCount.toLocaleString()}건`}
                    onMouseEnter={() => setActiveIndex(index)}
                    onMouseLeave={() => setActiveIndex(null)}
                    onFocus={() => setActiveIndex(index)}
                    onBlur={() => setActiveIndex(null)}
                  />
                </g>
              ))}
            </>
          ) : null}
          {ticks.map((index) => {
            const row = rows[index]
            if (!row) return null
            return (
              <text
                key={row.date}
                x={x(index)}
                y={CHART_HEIGHT - 8}
                textAnchor={index === 0 ? 'start' : index === rows.length - 1 ? 'end' : 'middle'}
                fill="var(--text-muted)"
                fontSize="11"
              >
                {formatPeriodDate(row.date, data.period.granularity)}
              </text>
            )
          })}
        </svg>
      </div>
    </section>
  )
}

function ScrapTrendChart({
  data,
  granularityOptions,
  loading,
  onGranularityChange,
}: {
  data: ProcessOverview
  granularityOptions: ProcessGranularity[]
  loading: boolean
  onGranularityChange: (granularity: ProcessGranularity) => void
}) {
  const [activeIndex, setActiveIndex] = useState<number | null>(null)
  const rows = data.trend
  const plotWidth = CHART_WIDTH - CHART_PADDING.left - CHART_PADDING.right
  const plotHeight = CHART_HEIGHT - CHART_PADDING.top - CHART_PADDING.bottom
  const dataMax = Math.max(1, ...rows.map((row) => row.scrappedQty))
  const axis = getAxisScale(dataMax)
  const slotWidth = rows.length > 0 ? plotWidth / rows.length : plotWidth
  const barWidth = Math.max(2, Math.min(18, slotWidth * 0.62))
  const ticks = tickIndexes(rows.length)
  const average = rows.length
    ? rows.reduce((sum, row) => sum + row.scrappedQty, 0) / rows.length
    : 0
  const y = (value: number) => CHART_PADDING.top + plotHeight - (value / axis.max) * plotHeight
  const activeRow = activeIndex === null ? null : rows[activeIndex]
  const activeCenterX =
    activeIndex === null
      ? CHART_WIDTH / 2
      : CHART_PADDING.left + slotWidth * activeIndex + slotWidth / 2

  return (
    <section className="min-w-0 rounded-[5px] border border-border bg-panel p-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h3 className="text-[13px] font-semibold text-text">폐기수량 추이</h3>
          <p className="mt-0.5 text-[10.5px] text-text-muted">
            작업지시 완료일 기준 · {granularityLabel(data.period.granularity)} 집계
          </p>
        </div>
        <GranularityToggle
          label="폐기수량 추이 집계 기준"
          value={data.period.granularity}
          options={granularityOptions}
          disabled={loading}
          onChange={onGranularityChange}
        />
      </div>
      <div className="relative mt-3 aspect-[720/236] w-full overflow-hidden">
        {activeRow ? (
          <ChartTooltip
            left={(activeCenterX / CHART_WIDTH) * 100}
            date={activeRow.date}
            granularity={data.period.granularity}
            values={[
              { label: '폐기수량', value: activeRow.scrappedQty, unit: '개', color: 'var(--warn)' },
            ]}
          />
        ) : null}
        <svg
          className="block size-full"
          viewBox={`0 0 ${CHART_WIDTH} ${CHART_HEIGHT}`}
          role="img"
          aria-label={`선택 기간의 ${granularityLabel(data.period.granularity)} 폐기수량 추이`}
        >
          <text x="8" y="13" fill="var(--text-muted)" fontSize="11">
            단위: 개
          </text>
          {axis.ticks.map((value, index) => {
            const gridY = CHART_PADDING.top + (plotHeight * index) / (axis.ticks.length - 1)
            return (
              <g key={value}>
                <line
                  x1={CHART_PADDING.left}
                  x2={CHART_WIDTH - CHART_PADDING.right}
                  y1={gridY}
                  y2={gridY}
                  stroke="var(--border)"
                  strokeWidth={value === 0 ? '1.2' : '1'}
                />
                <text
                  x={CHART_PADDING.left - 8}
                  y={gridY + 4}
                  textAnchor="end"
                  fill="var(--text-muted)"
                  fontSize="11"
                >
                  {value.toLocaleString()}
                </text>
              </g>
            )
          })}
          {average > 0 ? (
            <g>
              <line
                x1={CHART_PADDING.left}
                x2={CHART_WIDTH - CHART_PADDING.right}
                y1={y(average)}
                y2={y(average)}
                stroke="var(--text-muted)"
                strokeWidth="1"
                strokeDasharray="4 4"
              />
              <text
                x={CHART_WIDTH - CHART_PADDING.right}
                y={y(average) - 5}
                textAnchor="end"
                fill="var(--text-muted)"
                fontSize="10"
              >
                평균 {average.toFixed(1)}
              </text>
            </g>
          ) : null}
          {rows.map((row, index) => {
            const height = (row.scrappedQty / axis.max) * plotHeight
            const centerX = CHART_PADDING.left + slotWidth * index + slotWidth / 2
            return (
              <g key={row.date}>
                <rect
                  x={centerX - barWidth / 2}
                  y={CHART_PADDING.top + plotHeight - height}
                  width={barWidth}
                  height={height}
                  rx="2"
                  fill="var(--warn)"
                  opacity={activeIndex === null || activeIndex === index ? '1' : '0.42'}
                />
                <rect
                  x={CHART_PADDING.left + slotWidth * index}
                  y={CHART_PADDING.top}
                  width={slotWidth}
                  height={plotHeight}
                  fill="transparent"
                  tabIndex={0}
                  aria-label={`${row.date}, 폐기수량 ${row.scrappedQty.toLocaleString()}개`}
                  onMouseEnter={() => setActiveIndex(index)}
                  onMouseLeave={() => setActiveIndex(null)}
                  onFocus={() => setActiveIndex(index)}
                  onBlur={() => setActiveIndex(null)}
                />
              </g>
            )
          })}
          {ticks.map((index) => {
            const row = rows[index]
            if (!row) return null
            const centerX = CHART_PADDING.left + slotWidth * index + slotWidth / 2
            return (
              <text
                key={row.date}
                x={centerX}
                y={CHART_HEIGHT - 8}
                textAnchor={index === 0 ? 'start' : index === rows.length - 1 ? 'end' : 'middle'}
                fill="var(--text-muted)"
                fontSize="11"
              >
                {formatPeriodDate(row.date, data.period.granularity)}
              </text>
            )
          })}
        </svg>
      </div>
    </section>
  )
}

function LocationRanking({ data }: { data: ProcessOverview }) {
  const maxValue = Math.max(1, ...data.locations.map((location) => location.operationCount))

  return (
    <section className="rounded-[5px] border border-border bg-panel p-4 xl:col-span-2">
      <div>
        <h3 className="text-[13px] font-semibold text-text">작업장별 수행 공정</h3>
        <p className="mt-0.5 text-[10.5px] text-text-muted">
          선택 기간의 공정 수행 건수 기준 상위 작업장
        </p>
      </div>
      <div className="mt-4 grid gap-x-6 gap-y-3 sm:grid-cols-2 xl:grid-cols-3">
        {data.locations.map((location, index) => (
          <div
            key={location.locationId}
            className="grid min-w-0 grid-cols-[20px_minmax(90px,0.8fr)_minmax(80px,1.2fr)_auto] items-center gap-2.5"
          >
            <span className="text-right text-[10.5px] font-semibold tabular-nums text-text-faint">
              {index + 1}
            </span>
            <span
              className="truncate text-[11px] font-medium text-text"
              title={location.locationName}
            >
              {location.locationName}
            </span>
            <span className="h-1.5 overflow-hidden rounded-sm bg-panel-2" aria-hidden="true">
              <span
                className="block h-full rounded-sm bg-info"
                style={{ width: `${Math.max(4, (location.operationCount / maxValue) * 100)}%` }}
              />
            </span>
            <span className="text-right text-[11px] tabular-nums text-text-muted">
              {location.operationCount.toLocaleString()}건
            </span>
          </div>
        ))}
      </div>
    </section>
  )
}

export function ProcessOverviewSection() {
  const [data, setData] = useState<ProcessOverview | null>(null)
  const [fromDate, setFromDate] = useState('')
  const [toDate, setToDate] = useState('')
  const [granularity, setGranularity] = useState<ProcessGranularity>('day')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const controllerRef = useRef<AbortController | null>(null)

  const load = (
    options: {
      from?: string
      to?: string
      granularity?: ProcessGranularity
    } = {},
  ) => {
    controllerRef.current?.abort()
    const controller = new AbortController()
    controllerRef.current = controller
    setLoading(true)
    setError(null)
    fetchProcessOverview(options, controller.signal)
      .then((next) => {
        setData(next)
        setFromDate(next.period.from)
        setToDate(next.period.to)
        setGranularity(next.period.granularity)
      })
      .catch((fetchError: unknown) => {
        if (!controller.signal.aborted) {
          console.error('fetchProcessOverview failed:', fetchError)
          setError('공정 현황을 불러오지 못했습니다.')
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false)
      })
  }

  useEffect(() => {
    const controller = new AbortController()
    controllerRef.current = controller
    fetchProcessOverview({}, controller.signal)
      .then((next) => {
        setData(next)
        setFromDate(next.period.from)
        setToDate(next.period.to)
        setGranularity(next.period.granularity)
      })
      .catch((fetchError: unknown) => {
        if (!controller.signal.aborted) {
          console.error('fetchProcessOverview failed:', fetchError)
          setError('공정 현황을 불러오지 못했습니다.')
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false)
      })
    return () => controller.abort()
  }, [])

  const activePreset = useMemo<Preset | null>(() => {
    if (!data) return null
    if (fromDate === data.availableRange.from && toDate === data.availableRange.to) return 'all'
    if (toDate !== data.availableRange.to) return null
    const days =
      Math.round(
        (Date.parse(`${toDate}T00:00:00Z`) - Date.parse(`${fromDate}T00:00:00Z`)) / 86400000,
      ) + 1
    if (days === 7) return '7d'
    if (days === 30) return '30d'
    if (days === 90) return '90d'
    return null
  }, [data, fromDate, toDate])

  const granularityOptions = useMemo(
    () =>
      data
        ? getProcessGranularityOptions(data.period.from, data.period.to)
        : (['day'] as ProcessGranularity[]),
    [data],
  )

  const applyPreset = (preset: Preset) => {
    if (!data) return
    const to = data.availableRange.to
    const from =
      preset === 'all'
        ? data.availableRange.from
        : addDays(to, -(preset === '7d' ? 6 : preset === '30d' ? 29 : 89))
    const boundedFrom = from < data.availableRange.from ? data.availableRange.from : from
    load({
      from: boundedFrom,
      to,
      granularity: resolveProcessGranularity(granularity, boundedFrom, to),
    })
  }

  const submitPeriod = (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (fromDate && toDate) {
      load({
        from: fromDate,
        to: toDate,
        granularity: resolveProcessGranularity(granularity, fromDate, toDate),
      })
    }
  }

  const applyGranularity = (nextGranularity: ProcessGranularity) => {
    if (fromDate && toDate) {
      load({ from: fromDate, to: toDate, granularity: nextGranularity })
    }
  }

  return (
    <section aria-labelledby="process-overview-title" className="mt-5 border-t border-border pt-5">
      <div className="flex flex-col gap-3 xl:flex-row xl:items-end xl:justify-between">
        <div>
          <div className="flex items-center gap-2">
            <h2 id="process-overview-title" className="text-[15px] font-semibold text-text">
              공정 현황
            </h2>
            {data ? (
              <span className="rounded-sm border border-border bg-panel px-2 py-1 text-[10px] text-text-muted">
                {granularityLabel(data.period.granularity)} 집계
              </span>
            ) : null}
          </div>
          <p className="mt-1 text-[10.5px] text-text-muted">
            작업지시와 공정 실적은 선택한 기간에만 집계됩니다.
            {data ? ` 선택 가능 ${data.availableRange.from}–${data.availableRange.to}` : ''}
          </p>
        </div>
        <form
          onSubmit={submitPeriod}
          className="flex flex-wrap items-end gap-2"
          aria-label="공정 기간 선택"
        >
          <div className="flex rounded-[4px] border border-border bg-panel p-0.5">
            {(
              [
                ['7d', '7일'],
                ['30d', '30일'],
                ['90d', '3개월'],
                ['all', '전체'],
              ] as const
            ).map(([value, label]) => (
              <button
                key={value}
                type="button"
                onClick={() => applyPreset(value)}
                disabled={!data || loading}
                aria-pressed={activePreset === value}
                className="rounded-[3px] px-2.5 py-1.5 text-[10.5px] font-medium text-text-muted transition-colors hover:text-text aria-pressed:bg-accent-bg aria-pressed:text-info disabled:opacity-50"
              >
                {label}
              </button>
            ))}
          </div>
          <label className="grid gap-1 text-[10px] text-text-muted">
            시작일
            <input
              type="date"
              value={fromDate}
              min={data?.availableRange.from}
              max={toDate || data?.availableRange.to}
              onChange={(event) => setFromDate(event.target.value)}
              className="h-8 rounded-[4px] border border-border bg-panel px-2 text-[11px] text-text outline-none focus:border-info"
            />
          </label>
          <label className="grid gap-1 text-[10px] text-text-muted">
            종료일
            <input
              type="date"
              value={toDate}
              min={fromDate || data?.availableRange.from}
              max={data?.availableRange.to}
              onChange={(event) => setToDate(event.target.value)}
              className="h-8 rounded-[4px] border border-border bg-panel px-2 text-[11px] text-text outline-none focus:border-info"
            />
          </label>
          <Button
            type="submit"
            variant="outline"
            size="sm"
            disabled={!fromDate || !toDate || loading}
          >
            {loading ? <LoaderCircle className="animate-spin" /> : <CalendarDays />}
            적용
          </Button>
        </form>
      </div>

      {error && !data ? (
        <div className="mt-4 flex min-h-40 flex-col items-center justify-center gap-3 rounded-[5px] border border-fail/30 bg-panel text-center">
          <AlertTriangle className="size-5 text-fail" />
          <p className="text-[12px] text-fail">{error}</p>
          <Button type="button" variant="outline" size="sm" onClick={() => load()}>
            <RotateCcw /> 다시 시도
          </Button>
        </div>
      ) : data ? (
        <div
          className={loading ? 'mt-4 opacity-55 transition-opacity' : 'mt-4 transition-opacity'}
          aria-busy={loading}
        >
          <div className="grid grid-cols-2 gap-x-3 gap-y-4 rounded-[5px] border border-border bg-panel px-4 py-4 sm:grid-cols-3 xl:grid-cols-5">
            {data.kpis.map((kpi) => (
              <ProcessMetric key={kpi.key} kpi={kpi} />
            ))}
          </div>
          {error ? (
            <p className="mt-2 text-[11px] text-fail">{error} 기존 결과를 유지합니다.</p>
          ) : null}
          <div className="mt-4 grid grid-cols-1 gap-4 xl:grid-cols-2">
            <WorkOrderTrendChart
              data={data}
              granularityOptions={granularityOptions}
              loading={loading}
              onGranularityChange={applyGranularity}
            />
            <ScrapTrendChart
              data={data}
              granularityOptions={granularityOptions}
              loading={loading}
              onGranularityChange={applyGranularity}
            />
            <LocationRanking data={data} />
          </div>
        </div>
      ) : (
        <div className="mt-4 h-80 animate-pulse rounded-[5px] border border-border bg-panel" />
      )}
    </section>
  )
}
