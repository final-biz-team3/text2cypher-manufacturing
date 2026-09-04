import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import { AnswerVisualization } from './AnswerVisualization'

describe('AnswerVisualization', () => {
  it('renders KPI cards with labels and formatted values', () => {
    const html = renderToStaticMarkup(
      <AnswerVisualization
        visualization={{
          type: 'kpi',
          title: 'Touring-1000 Yellow, 54',
          items: [
            { label: '정가', value: 2384.07 },
            { label: '표준원가', value: 1912.42 },
          ],
        }}
      />,
    )

    expect(html).toContain('정가')
    expect(html).toContain('2,384.07')
    expect(html).toContain('표준원가')
    expect(html).toContain('1,912.42')
    expect(html).toContain('Touring-1000 Yellow, 54')
    expect(html).toContain('role="img"')
  })

  it('renders a responsive bar chart wrapper for ranked rows', () => {
    const html = renderToStaticMarkup(
      <AnswerVisualization
        visualization={{
          type: 'bar',
          title: null,
          categoryLabel: '제품명',
          series: [{ key: 'value', label: '판매량' }],
          data: [
            { category: 'Product A', value: 8420 },
            { category: 'Product B', value: 6830 },
          ],
        }}
      />,
    )

    // Recharts의 ResponsiveContainer는 실제 브라우저의 크기 측정에 의존해서
    // renderToStaticMarkup(SSR)로는 내부 SVG/막대까지 그려지지 않는다 -
    // 여기서는 컴포넌트가 올바른 래퍼를 렌더링하는지만 확인한다. 실제 막대
    // 렌더링 여부는 Task 8의 브라우저 수동 확인에서 검증한다.
    expect(html).toContain('recharts-responsive-container')
    expect(html).toContain('제품명')
    expect(html).toContain('판매량')
    expect(html).toContain('role="img"')
  })

  it('renders nothing when kpi items are empty', () => {
    const html = renderToStaticMarkup(
      <AnswerVisualization visualization={{ type: 'kpi', title: null, items: [] }} />,
    )

    expect(html).toBe('')
  })

  it('renders nothing when bar data is empty', () => {
    const html = renderToStaticMarkup(
      <AnswerVisualization
        visualization={{
          type: 'bar',
          title: null,
          categoryLabel: '제품명',
          series: [{ key: 'value', label: '판매량' }],
          data: [],
        }}
      />,
    )

    expect(html).toBe('')
  })

  it('renders ranked progress items with a fulfillment-width bar and shortage badge color', () => {
    const html = renderToStaticMarkup(
      <AnswerVisualization
        visualization={{
          type: 'ranked_progress',
          title: null,
          entityLabel: 'Product',
          unit: '개',
          rankedItems: [
            {
              rank: 1,
              title: 'Frame Weld',
              actual: 10,
              required: 50,
              shortageQty: 40,
              fulfillmentPct: 20,
            },
            {
              rank: 2,
              title: 'Seat Post',
              actual: 20,
              required: 20,
              shortageQty: 0,
              fulfillmentPct: 100,
            },
          ],
        }}
      />,
    )

    expect(html).toContain('Frame Weld')
    expect(html).toContain('20%')
    expect(html).toContain('40개 부족')
    expect(html).toContain('width:20%')
    expect(html).toContain('var(--color-destructive)')
    expect(html).toContain('Seat Post')
    expect(html).toContain('width:100%')
    expect(html).toContain('#3BB2BF')
    expect(html).toContain('role="img"')
    expect(html).toContain('제품')
  })

  it('renders nothing when ranked progress items are empty', () => {
    const html = renderToStaticMarkup(
      <AnswerVisualization
        visualization={{ type: 'ranked_progress', title: null, rankedItems: [] }}
      />,
    )

    expect(html).toBe('')
  })

  it('renders a histogram wrapper with bin labels', () => {
    const html = renderToStaticMarkup(
      <AnswerVisualization
        visualization={{
          type: 'histogram',
          title: null,
          categoryLabel: '정가 구간',
          series: [{ key: 'value', label: '건수' }],
          data: [
            { category: '0~500', value: 4 },
            { category: '500~1000', value: 7 },
          ],
        }}
      />,
    )

    expect(html).toContain('정가 구간')
    expect(html).toContain('recharts-responsive-container')
    expect(html).toContain('role="img"')
  })

  it('renders a scatter plot wrapper with axis labels', () => {
    const html = renderToStaticMarkup(
      <AnswerVisualization
        visualization={{
          type: 'scatter',
          title: null,
          xLabel: '정가',
          yLabel: '표준원가',
          points: [
            { x: 1200, y: 800, label: 'Product A' },
            { x: 900, y: 650, label: 'Product B' },
            { x: 1500, y: 1100, label: 'Product C' },
          ],
        }}
      />,
    )

    expect(html).toContain('정가 vs 표준원가')
    expect(html).toContain('recharts-responsive-container')
    expect(html).toContain('role="img"')
  })

  it('renders nothing when scatter points are empty', () => {
    const html = renderToStaticMarkup(
      <AnswerVisualization
        visualization={{ type: 'scatter', title: null, xLabel: 'x', yLabel: 'y', points: [] }}
      />,
    )

    expect(html).toBe('')
  })
})
