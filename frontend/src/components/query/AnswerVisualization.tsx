import { Tag } from 'lucide-react'
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

// 규칙 기반으로 결정된 시각화 스펙(KPI 카드·막대그래프·순위 진행률·히스토그램·
// 산점도)을 렌더링한다.
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
