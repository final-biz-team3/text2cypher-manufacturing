import { Tag } from 'lucide-react'
import { useRef, useState } from 'react'
import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import type { VisualizationSpec } from '@/lib/schemas'

interface AnswerVisualizationProps {
  visualization: VisualizationSpec
}

const numberFormatter = new Intl.NumberFormat('ko-KR')

const ENTITY_LABEL_KO: Record<NonNullable<VisualizationSpec['entityLabel']>, string> = {
  Product: '제품',
  Supplier: '공급업체',
  WorkOrder: '작업지시',
  RoutingOperation: '공정',
  Location: '작업장',
  ScrapReason: '폐기사유',
}

// Tailwind는 클래스명을 소스에 나온 리터럴 문자열로만 스캔하므로, NODE_COLOR_CLASS
// 값을 런타임에 .replace('bg-','border-')로 조합하면 border-node-* 유틸리티가
// 생성되지 않는다 - 별도로 리터럴 맵을 둔다.
const NODE_BORDER_CLASS: Record<NonNullable<VisualizationSpec['entityLabel']>, string> = {
  Product: 'border-node-product',
  Supplier: 'border-node-supplier',
  WorkOrder: 'border-node-workorder',
  RoutingOperation: 'border-node-routingoperation',
  Location: 'border-node-location',
  ScrapReason: 'border-node-scrapreason',
}

function formatWithUnit(value: number, unit: string | null | undefined) {
  const formatted = numberFormatter.format(value)
  return unit ? `${formatted}${unit}` : formatted
}

const axisLabelStyle = { fontSize: 10, fill: 'var(--color-text-muted)' }

// 축이 무엇을 나타내는지(필드명 + 단위)를 축 제목으로 합친다. 예: "판매량 (개)".
function axisTitle(label: string | null | undefined, unit: string | null | undefined) {
  if (!label) return unit ?? undefined
  return unit ? `${label} (${unit})` : label
}

function KpiCards({
  title,
  items,
}: {
  title: VisualizationSpec['title']
  items: NonNullable<VisualizationSpec['items']>
}) {
  if (items.length === 0) return null
  const summary = items
    .map((item) => `${item.label} ${numberFormatter.format(item.value)}`)
    .join(', ')
  const ariaLabel = title ? `${title} KPI: ${summary}` : `KPI: ${summary}`
  return (
    <div role="img" aria-label={ariaLabel} className="mb-3">
      {title ? <p className="mb-1 text-[10.5px] text-text-muted">{title}</p> : null}
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        {items.map((item) => (
          <div
            key={item.label}
            className="rounded-md border-[1.5px] border-border-strong bg-panel px-3 py-2"
          >
            <p className="text-[10.5px] text-text-muted">{item.label}</p>
            <p className="mt-0.5 text-base font-semibold text-text">
              {numberFormatter.format(item.value)}
            </p>
          </div>
        ))}
      </div>
    </div>
  )
}

function BarVisualization({
  categoryLabel,
  data,
  series,
}: {
  categoryLabel: VisualizationSpec['categoryLabel']
  data: NonNullable<VisualizationSpec['data']>
  series: NonNullable<VisualizationSpec['series']>[number]
}) {
  if (data.length === 0) return null
  const caption = categoryLabel ? `${categoryLabel}별 ${series.label}` : series.label
  return (
    <div
      role="img"
      aria-label={`${caption} 막대그래프`}
      className="mb-3 rounded-md border border-border bg-panel p-3"
    >
      <p className="mb-2 text-[10.5px] text-text-muted">{caption}</p>
      <ResponsiveContainer width="100%" height={Math.max(120, data.length * 32)}>
        <BarChart data={data} layout="vertical" margin={{ top: 4, right: 16, bottom: 4, left: 8 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border)" horizontal={false} />
          <XAxis
            type="number"
            tick={{ fill: 'var(--color-text-muted)', fontSize: 11 }}
            label={{
              value: axisTitle(series.label, series.unit),
              position: 'insideBottom',
              offset: -2,
              ...axisLabelStyle,
            }}
          />
          <YAxis
            type="category"
            dataKey="category"
            width={110}
            tick={{ fill: 'var(--color-text)', fontSize: 11 }}
          />
          <Tooltip
            formatter={(value: number) => formatWithUnit(value, series.unit)}
            contentStyle={{
              background: 'var(--color-panel)',
              border: '1px solid var(--color-border)',
              fontSize: 12,
            }}
          />
          <Bar dataKey={series.key} name={series.label} fill="#3BB2BF" radius={[0, 4, 4, 0]} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  )
}

// 첫 색은 옅은 트랙 배경(bg-border) 위에 놓이므로 트랙과 뚜렷이 구분되는
// 진한 회색을 쓴다 - 트랙과 같은 톤이면 두꺼운 막대가 배경에 묻혀 안 보인다.
const COMPARISON_BAR_COLORS = ['#8A94A3', '#3BB2BF'] as const

interface ComparisonBarSegment {
  pct: number
  color: string
  label: string
  value: number
  unit?: string | null
}

// 두 막대는 높이가 같아야 "겹쳐진 막대"로 읽힌다 - 값이 더 큰 쪽(back)을
// 먼저 그려 전체 트랙을 채우고, 더 작은 쪽(front)을 그 위에 덧그려서
// back은 front보다 긴 구간에서만 보이게 한다. 어느 시리즈가 더 큰지는
// 행마다 달라질 수 있어 값 기준으로 매번 정렬한다.
function ComparisonBarRow({
  category,
  back,
  front,
}: {
  category: string
  back: ComparisonBarSegment
  front: ComparisonBarSegment
}) {
  const trackRef = useRef<HTMLDivElement>(null)
  const [hover, setHover] = useState<{ x: number; y: number; text: string } | null>(null)

  const handleMove = (event: React.MouseEvent, segment: ComparisonBarSegment) => {
    const rect = trackRef.current?.getBoundingClientRect()
    if (!rect) return
    setHover({
      x: event.clientX - rect.left,
      y: event.clientY - rect.top,
      text: `${segment.label}: ${formatWithUnit(segment.value, segment.unit)}`,
    })
  }

  return (
    <div className="flex flex-col gap-1">
      <p className="text-[11px] text-text">{category}</p>
      {/* 오른쪽 끝은 값이 domainMax에 못 미치면(같은 행이 아닌 다른 행이 더
          클 수 있음) 트랙 배경이 살짝 보이는 게 정상이다 - 다만 막대와
          트랙이 각자 오른쪽 모서리를 따로 둥글리면 그 경계에서 트랙의
          둥근 모서리 조각이 어긋나 보인다. 왼쪽만 둥글리고 오른쪽은
          네모로 둬서 이 어긋남 자체를 없앤다. */}
      <div ref={trackRef} className="relative h-4 w-full rounded-l-sm bg-border">
        <div
          className="absolute inset-y-0 left-0 rounded-l-sm"
          style={{ width: `${back.pct}%`, background: back.color }}
          onMouseMove={(event) => handleMove(event, back)}
          onMouseLeave={() => setHover(null)}
        />
        <div
          className="absolute inset-y-0 left-0 rounded-l-sm"
          style={{ width: `${front.pct}%`, background: front.color }}
          onMouseMove={(event) => handleMove(event, front)}
          onMouseLeave={() => setHover(null)}
        />
        {hover ? (
          <div
            className="pointer-events-none absolute z-10 -translate-x-1/2 -translate-y-full whitespace-nowrap rounded-sm border border-border bg-panel px-1.5 py-0.5 text-[10px] text-text shadow-sm"
            style={{ left: hover.x, top: hover.y - 6 }}
          >
            {hover.text}
          </div>
        ) : null}
      </div>
    </div>
  )
}

function ComparisonBarChart({
  categoryLabel,
  data,
  series,
}: {
  categoryLabel: VisualizationSpec['categoryLabel']
  data: NonNullable<VisualizationSpec['data']>
  series: NonNullable<VisualizationSpec['series']>
}) {
  if (data.length === 0 || series.length < 2) return null
  const [seriesA, seriesB] = series
  const caption = categoryLabel
    ? `${categoryLabel}별 ${seriesA.label} vs ${seriesB.label}`
    : `${seriesA.label} vs ${seriesB.label}`
  const domainMax = Math.max(
    1,
    ...data.flatMap((row) => [Number(row[seriesA.key]) || 0, Number(row[seriesB.key]) || 0]),
  )
  const summary = data
    .map(
      (row) =>
        `${String(row.category)} ${seriesA.label} ${formatWithUnit(Number(row[seriesA.key]) || 0, seriesA.unit)}, ${seriesB.label} ${formatWithUnit(Number(row[seriesB.key]) || 0, seriesB.unit)}`,
    )
    .join(', ')
  return (
    <div
      role="img"
      aria-label={`${caption}: ${summary}`}
      className="mb-3 flex flex-col gap-2 rounded-md border border-border bg-panel p-3"
    >
      <div className="flex items-center justify-between gap-2">
        <p className="text-[10.5px] text-text-muted">{caption}</p>
        <div className="flex items-center gap-3 text-[10.5px] text-text-muted">
          <span className="flex items-center gap-1">
            <span
              className="inline-block size-2 rounded-full"
              style={{ background: COMPARISON_BAR_COLORS[0] }}
            />
            {seriesA.label}
          </span>
          <span className="flex items-center gap-1">
            <span
              className="inline-block size-2 rounded-full"
              style={{ background: COMPARISON_BAR_COLORS[1] }}
            />
            {seriesB.label}
          </span>
        </div>
      </div>
      <div className="flex flex-col gap-2.5">
        {data.map((row) => {
          const aValue = Number(row[seriesA.key]) || 0
          const bValue = Number(row[seriesB.key]) || 0
          const aSegment: ComparisonBarSegment = {
            pct: Math.max(0, Math.min(100, (aValue / domainMax) * 100)),
            color: COMPARISON_BAR_COLORS[0],
            label: seriesA.label,
            value: aValue,
            unit: seriesA.unit,
          }
          const bSegment: ComparisonBarSegment = {
            pct: Math.max(0, Math.min(100, (bValue / domainMax) * 100)),
            color: COMPARISON_BAR_COLORS[1],
            label: seriesB.label,
            value: bValue,
            unit: seriesB.unit,
          }
          const [back, front] = aValue >= bValue ? [aSegment, bSegment] : [bSegment, aSegment]
          return (
            <ComparisonBarRow
              key={String(row.category)}
              category={String(row.category)}
              back={back}
              front={front}
            />
          )
        })}
      </div>
    </div>
  )
}

function RankedProgress({
  items,
  entityLabel,
  unit,
}: {
  items: NonNullable<VisualizationSpec['rankedItems']>
  entityLabel: VisualizationSpec['entityLabel']
  unit: VisualizationSpec['unit']
}) {
  if (items.length === 0) return null
  const summary = items
    .map((item) => `${item.rank}위 ${item.title} ${item.fulfillmentPct}%`)
    .join(', ')
  const entityLabelKo = entityLabel ? ENTITY_LABEL_KO[entityLabel] : null
  const entityBorderClass = entityLabel ? NODE_BORDER_CLASS[entityLabel] : null
  return (
    <div
      role="img"
      aria-label={`${entityLabelKo ? `${entityLabelKo} ` : ''}부족 순위: ${summary}`}
      className="mb-3 flex flex-col gap-2"
    >
      {entityLabelKo ? (
        <span
          className={`inline-flex w-fit items-center gap-1 rounded-full border-[1.5px] bg-panel px-2 py-0.5 text-[10.5px] font-medium text-text ${entityBorderClass}`}
        >
          <Tag size={10} aria-hidden="true" />
          {entityLabelKo}
        </span>
      ) : null}
      {items.map((item) => (
        <div key={item.rank} className="rounded-md border border-border bg-panel px-3 py-2">
          <div className="flex items-baseline justify-between gap-2">
            <p className="text-[12.5px] font-semibold text-text">
              {item.rank}위 {item.title}
            </p>
            <p className="text-[10.5px] text-text-muted">
              보유 {formatWithUnit(item.actual, unit)} / 필요 {formatWithUnit(item.required, unit)}
            </p>
          </div>
          <div className="mt-1.5 h-1.5 w-full overflow-hidden rounded-full bg-border">
            <div
              className="h-full rounded-full"
              style={{
                width: `${item.fulfillmentPct}%`,
                background: item.fulfillmentPct < 100 ? 'var(--color-destructive)' : '#3BB2BF',
              }}
            />
          </div>
          <p className="mt-1 text-[10.5px] text-text-muted">
            {item.fulfillmentPct}% 충족
            {item.shortageQty > 0 ? ` · ${formatWithUnit(item.shortageQty, unit)} 부족` : null}
          </p>
        </div>
      ))}
    </div>
  )
}

function HistogramChart({
  categoryLabel,
  data,
  series,
}: {
  categoryLabel: VisualizationSpec['categoryLabel']
  data: NonNullable<VisualizationSpec['data']>
  series: NonNullable<VisualizationSpec['series']>[number]
}) {
  if (data.length === 0) return null
  const caption = categoryLabel ?? series.label
  return (
    <div
      role="img"
      aria-label={`${caption} 히스토그램`}
      className="mb-3 rounded-md border border-border bg-panel p-3"
    >
      <p className="mb-2 text-[10.5px] text-text-muted">{caption}</p>
      <ResponsiveContainer width="100%" height={200}>
        <BarChart data={data} barCategoryGap={2} margin={{ top: 4, right: 8, bottom: 4, left: 8 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border)" vertical={false} />
          <XAxis
            dataKey="category"
            tick={{ fill: 'var(--color-text-muted)', fontSize: 10 }}
            interval={0}
            angle={-30}
            textAnchor="end"
            height={48}
          />
          <YAxis
            type="number"
            allowDecimals={false}
            tick={{ fill: 'var(--color-text-muted)', fontSize: 11 }}
            label={{
              value: axisTitle(series.label, series.unit),
              angle: -90,
              position: 'insideLeft',
              ...axisLabelStyle,
            }}
          />
          <Tooltip
            formatter={(value: number) => formatWithUnit(value, series.unit)}
            contentStyle={{
              background: 'var(--color-panel)',
              border: '1px solid var(--color-border)',
              fontSize: 12,
            }}
          />
          <Bar dataKey={series.key} name={series.label} fill="#3BB2BF" radius={[2, 2, 0, 0]} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  )
}

function ScatterPlot({
  xLabel,
  yLabel,
  xUnit,
  yUnit,
  points,
}: {
  xLabel: VisualizationSpec['xLabel']
  yLabel: VisualizationSpec['yLabel']
  xUnit: VisualizationSpec['xUnit']
  yUnit: VisualizationSpec['yUnit']
  points: NonNullable<VisualizationSpec['points']>
}) {
  if (points.length === 0) return null
  const caption = xLabel && yLabel ? `${xLabel} vs ${yLabel}` : '산점도'
  return (
    <div
      role="img"
      aria-label={`${caption} 산점도`}
      className="mb-3 rounded-md border border-border bg-panel p-3"
    >
      <p className="mb-2 text-[10.5px] text-text-muted">{caption}</p>
      <ResponsiveContainer width="100%" height={220}>
        <ScatterChart margin={{ top: 4, right: 16, bottom: 20, left: 12 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border)" />
          <XAxis
            type="number"
            dataKey="x"
            name={xLabel ?? 'x'}
            tick={{ fill: 'var(--color-text-muted)', fontSize: 11 }}
            label={{
              value: axisTitle(xLabel, xUnit),
              position: 'insideBottom',
              offset: -2,
              ...axisLabelStyle,
            }}
          />
          <YAxis
            type="number"
            dataKey="y"
            name={yLabel ?? 'y'}
            tick={{ fill: 'var(--color-text-muted)', fontSize: 11 }}
            label={{
              value: axisTitle(yLabel, yUnit),
              angle: -90,
              position: 'insideLeft',
              ...axisLabelStyle,
            }}
          />
          <Tooltip
            cursor={{ strokeDasharray: '3 3' }}
            formatter={(value: number, name: string) =>
              formatWithUnit(value, name === xLabel ? xUnit : yUnit)
            }
            contentStyle={{
              background: 'var(--color-panel)',
              border: '1px solid var(--color-border)',
              fontSize: 12,
            }}
          />
          <Scatter data={points} fill="#3BB2BF" />
        </ScatterChart>
      </ResponsiveContainer>
    </div>
  )
}

// 규칙 기반으로 결정된 시각화 스펙(KPI 카드·막대그래프·비교 막대그래프·순위
// 진행률·히스토그램·산점도)을 렌더링한다.
export function AnswerVisualization({ visualization }: AnswerVisualizationProps) {
  if (visualization.type === 'kpi') {
    return <KpiCards title={visualization.title} items={visualization.items ?? []} />
  }
  if (visualization.type === 'ranked_progress') {
    return (
      <RankedProgress
        items={visualization.rankedItems ?? []}
        entityLabel={visualization.entityLabel}
        unit={visualization.unit}
      />
    )
  }
  if (visualization.type === 'scatter') {
    return (
      <ScatterPlot
        xLabel={visualization.xLabel}
        yLabel={visualization.yLabel}
        xUnit={visualization.xUnit}
        yUnit={visualization.yUnit}
        points={visualization.points ?? []}
      />
    )
  }
  if (visualization.type === 'comparison_bar') {
    const series = visualization.series ?? []
    if (series.length < 2) return null
    return (
      <ComparisonBarChart
        categoryLabel={visualization.categoryLabel}
        data={visualization.data ?? []}
        series={series}
      />
    )
  }
  const series = visualization.series?.[0]
  if (!series) return null
  if (visualization.type === 'histogram') {
    return (
      <HistogramChart
        categoryLabel={visualization.categoryLabel}
        data={visualization.data ?? []}
        series={series}
      />
    )
  }
  return (
    <BarVisualization
      categoryLabel={visualization.categoryLabel}
      data={visualization.data ?? []}
      series={series}
    />
  )
}
