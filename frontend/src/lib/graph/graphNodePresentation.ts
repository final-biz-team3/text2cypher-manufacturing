import type { NodeHoverDrawingFunction, NodeLabelDrawingFunction } from 'sigma/rendering'
import type { GraphAttributes, GraphEdgeAttributes, GraphNodeAttributes } from '@/types/graph'

const CATEGORY_LABELS: Record<string, string> = {
  Product: '제품',
  Supplier: '공급업체',
  WorkOrder: '작업지시',
  RoutingOperation: '공정',
  Location: '작업장',
  ScrapReason: '폐기사유',
}

function firstValue(
  properties: Record<string, unknown>,
  keys: readonly string[],
): string | number | undefined {
  for (const key of keys) {
    const value = properties[key]
    if (typeof value === 'string' && value.trim()) return value
    if (typeof value === 'number' && Number.isFinite(value)) return value
  }
  return undefined
}

function formatNumber(value: string | number | undefined): string | null {
  if (typeof value === 'number') return value.toLocaleString('ko-KR')
  if (typeof value === 'string' && value.trim()) return value
  return null
}

function idText(properties: Record<string, unknown>, keys: readonly string[]): string | null {
  const value = formatNumber(firstValue(properties, keys))
  return value ? `ID ${value}` : null
}

function metricText(
  properties: Record<string, unknown>,
  keys: readonly string[],
  label: string,
  unit: string,
): string | null {
  const value = formatNumber(firstValue(properties, keys))
  return value ? `${label} ${value}${unit}` : null
}

export interface GraphNodePresentation {
  categoryLabel: string
  displayTitle: string
  displayMeta: string
}

export function createGraphNodePresentation(
  category: string,
  label: string,
  properties: Record<string, unknown>,
): GraphNodePresentation {
  const categoryLabel = CATEGORY_LABELS[category] ?? category
  const details: Array<string | null> = []

  if (category === 'Product') {
    details.push(
      formatNumber(firstValue(properties, ['productNumber']))
        ? `제품번호 ${formatNumber(firstValue(properties, ['productNumber']))}`
        : idText(properties, ['productId', 'componentId', 'finishedProductId', 'rootProductId']),
    )
    details.push(
      metricText(properties, ['shortageQty'], '부족', '개') ??
        metricText(properties, ['actualStock', 'stockQty', 'quantity'], '재고', '개'),
    )
  } else if (category === 'Supplier') {
    details.push(idText(properties, ['supplierId']))
    details.push(
      metricText(properties, ['suppliedProductCount', 'productCount'], '공급 제품', '종') ??
        metricText(properties, ['totalRejectedQty', 'rejectedQty'], '반려', '개'),
    )
  } else if (category === 'WorkOrder') {
    details.push(idText(properties, ['workOrderId']))
    details.push(metricText(properties, ['scrappedQty'], '폐기', '개'))
  } else if (category === 'RoutingOperation') {
    details.push(metricText(properties, ['operationSequence', 'sequence'], '순서', ''))
    details.push(
      formatNumber(firstValue(properties, ['locationName']))
        ? `작업장 ${formatNumber(firstValue(properties, ['locationName']))}`
        : idText(properties, ['locationId']),
    )
  } else if (category === 'Location') {
    details.push(idText(properties, ['locationId']))
    details.push(
      metricText(properties, ['workOrderCount'], '작업지시', '건') ??
        metricText(properties, ['operationCount'], '공정', '건'),
    )
  } else if (category === 'ScrapReason') {
    details.push(idText(properties, ['scrapReasonId']))
    details.push(metricText(properties, ['scrappedQty', 'totalScrappedQty'], '폐기', '개'))
  } else {
    details.push(idText(properties, ['id']))
  }

  return {
    categoryLabel,
    displayTitle: label,
    displayMeta: details
      .filter((detail): detail is string => Boolean(detail))
      .slice(0, 2)
      .join(' · '),
  }
}

function fitText(context: CanvasRenderingContext2D, value: string, maxWidth: number): string {
  if (context.measureText(value).width <= maxWidth) return value
  let low = 0
  let high = value.length
  while (low < high) {
    const middle = Math.ceil((low + high) / 2)
    if (context.measureText(`${value.slice(0, middle)}…`).width <= maxWidth) low = middle
    else high = middle - 1
  }
  return `${value.slice(0, low)}…`
}

function roundedRect(
  context: CanvasRenderingContext2D,
  x: number,
  y: number,
  width: number,
  height: number,
  radius: number,
) {
  const right = x + width
  const bottom = y + height
  context.beginPath()
  context.moveTo(x + radius, y)
  context.lineTo(right - radius, y)
  context.quadraticCurveTo(right, y, right, y + radius)
  context.lineTo(right, bottom - radius)
  context.quadraticCurveTo(right, bottom, right - radius, bottom)
  context.lineTo(x + radius, bottom)
  context.quadraticCurveTo(x, bottom, x, bottom - radius)
  context.lineTo(x, y + radius)
  context.quadraticCurveTo(x, y, x + radius, y)
  context.closePath()
}

interface LabelCardData {
  x: number
  y: number
  size: number
  color: string
  label: string | null
  categoryLabel?: unknown
  displayTitle?: unknown
  displayMeta?: unknown
  labelBackground?: unknown
  labelBorderColor?: unknown
  labelTextColor?: unknown
  labelMutedColor?: unknown
}

function drawLabelCard(
  context: CanvasRenderingContext2D,
  data: LabelCardData,
  emphasized: boolean,
) {
  const title = typeof data.displayTitle === 'string' ? data.displayTitle : (data.label ?? '')
  if (!title) return
  const category = typeof data.categoryLabel === 'string' ? data.categoryLabel : ''
  const meta = typeof data.displayMeta === 'string' ? data.displayMeta : ''
  const secondLine = [category, meta].filter(Boolean).join(' · ')
  const cardWidth = emphasized ? 184 : 166
  const cardHeight = secondLine ? 43 : 31
  const cardX = data.x + data.size + 7
  const cardY = data.y - cardHeight / 2
  const background =
    typeof data.labelBackground === 'string' ? data.labelBackground : 'rgba(255,255,255,0.94)'
  const border = typeof data.labelBorderColor === 'string' ? data.labelBorderColor : '#d7dce2'
  const text = typeof data.labelTextColor === 'string' ? data.labelTextColor : '#1a1d21'
  const muted = typeof data.labelMutedColor === 'string' ? data.labelMutedColor : '#6b7280'

  context.save()
  if (emphasized) {
    context.shadowColor = 'rgba(15,23,42,0.18)'
    context.shadowBlur = 12
    context.shadowOffsetY = 3
  }
  roundedRect(context, cardX, cardY, cardWidth, cardHeight, 5)
  context.fillStyle = background
  context.fill()
  context.shadowColor = 'transparent'
  context.strokeStyle = emphasized ? data.color : border
  context.lineWidth = emphasized ? 1.5 : 1
  context.stroke()

  roundedRect(context, cardX, cardY, 4, cardHeight, 2)
  context.fillStyle = data.color
  context.fill()

  context.font = `600 ${emphasized ? 12 : 11}px Pretendard Variable, Pretendard, sans-serif`
  context.fillStyle = text
  context.textBaseline = 'middle'
  context.fillText(fitText(context, title, cardWidth - 20), cardX + 12, cardY + 14)

  if (secondLine) {
    context.font = '500 9.5px Pretendard Variable, Pretendard, sans-serif'
    context.fillStyle = muted
    context.fillText(fitText(context, secondLine, cardWidth - 20), cardX + 12, cardY + 30)
  }
  context.restore()
}

function drawCompactLabel(context: CanvasRenderingContext2D, data: LabelCardData) {
  const title = typeof data.displayTitle === 'string' ? data.displayTitle : (data.label ?? '')
  if (!title) return
  const category = typeof data.categoryLabel === 'string' ? data.categoryLabel : ''
  const meta = typeof data.displayMeta === 'string' ? data.displayMeta : ''
  const secondLine = [category, meta].filter(Boolean).join(' · ')
  const textX = data.x + data.size + 5
  const textY = data.y - (secondLine ? 5 : 0)
  const textColor = typeof data.labelTextColor === 'string' ? data.labelTextColor : '#1a1d21'
  const mutedColor = typeof data.labelMutedColor === 'string' ? data.labelMutedColor : '#6b7280'
  const haloColor =
    typeof data.labelBackground === 'string' ? data.labelBackground : 'rgba(255,255,255,0.94)'

  context.save()
  context.textBaseline = 'middle'
  context.lineJoin = 'round'
  context.lineWidth = 3.5
  context.strokeStyle = haloColor
  context.font = '600 10.5px Pretendard Variable, Pretendard, sans-serif'
  const fittedTitle = fitText(context, title, 112)
  context.strokeText(fittedTitle, textX, textY)
  context.fillStyle = textColor
  context.fillText(fittedTitle, textX, textY)

  if (secondLine) {
    context.lineWidth = 3
    context.font = '500 8.5px Pretendard Variable, Pretendard, sans-serif'
    const fittedMeta = fitText(context, secondLine, 118)
    context.strokeText(fittedMeta, textX, textY + 12)
    context.fillStyle = mutedColor
    context.fillText(fittedMeta, textX, textY + 12)
  }
  context.restore()
}

export const drawGraphNodeLabel: NodeLabelDrawingFunction<
  GraphNodeAttributes,
  GraphEdgeAttributes,
  GraphAttributes
> = (context, data) => drawCompactLabel(context, data)

export const drawGraphNodeHover: NodeHoverDrawingFunction<
  GraphNodeAttributes,
  GraphEdgeAttributes,
  GraphAttributes
> = (context, data) => {
  context.save()
  context.globalAlpha = 0.2
  context.fillStyle = data.color
  context.beginPath()
  context.arc(data.x, data.y, data.size + 6, 0, Math.PI * 2)
  context.fill()
  context.restore()
  drawLabelCard(context, data, true)
}
