import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import { NaturalLanguageAnswerBox } from './NaturalLanguageAnswerBox'

describe('NaturalLanguageAnswerBox', () => {
  it('renders paragraphs, GFM lists, emphasis, and tables', () => {
    const html = renderToStaticMarkup(
      <NaturalLanguageAnswerBox
        answer={`**핵심 결과**입니다.

- 절단
- 용접

| 부품 | 수량 |
| --- | ---: |
| 프레임 | 2 |`}
      />,
    )

    expect(html).toContain('AI 정리 답변')
    expect(html).toContain('조회된 데이터만 근거로 정리했습니다.')
    expect(html).toContain('>핵심 결과</strong>')
    expect(html).toContain('<ul')
    expect(html).toContain('<table')
    expect(html).toContain('overflow-x-auto')
    expect(html).toContain('<td')
  })

  it('does not execute raw HTML or expose model-provided links', () => {
    const html = renderToStaticMarkup(
      <NaturalLanguageAnswerBox
        answer={'안전한 답변 <script>alert("x")</script> [외부 링크](https://example.com)'}
      />,
    )

    expect(html).not.toContain('<script')
    expect(html).not.toContain('href=')
    expect(html).toContain('<span>외부 링크</span>')
  })

  it('renders the visualization above the markdown answer when present', () => {
    const html = renderToStaticMarkup(
      <NaturalLanguageAnswerBox
        answer="판매량 상위 3개 제품입니다."
        visualization={{
          type: 'bar',
          title: null,
          categoryLabel: '제품명',
          series: [{ key: 'value', label: '판매량' }],
          data: [{ category: 'Product A', value: 8420 }],
        }}
      />,
    )

    expect(html).toContain('recharts-responsive-container')
    const chartIndex = html.indexOf('recharts-responsive-container')
    const answerIndex = html.indexOf('판매량 상위 3개 제품입니다')
    expect(chartIndex).toBeGreaterThan(-1)
    expect(chartIndex).toBeLessThan(answerIndex)
  })

  it('renders no visualization block when visualization is absent', () => {
    const html = renderToStaticMarkup(<NaturalLanguageAnswerBox answer="일반 답변입니다." />)

    expect(html).not.toContain('recharts')
  })

  it('renders the graph below the markdown answer when a cypher result has more than one row', () => {
    const html = renderToStaticMarkup(
      <NaturalLanguageAnswerBox
        answer="부품들의 공급업체는 다음과 같습니다."
        hasGraphResult
        graphRows={[
          { supplierId: 1, supplierName: 'Acme' },
          { supplierId: 2, supplierName: 'Globex' },
        ]}
        graphError={null}
        graphEmptyReason={null}
      />,
    )

    const graphIndex = html.indexOf('그래프 렌더러를 불러오는 중입니다')
    const answerIndex = html.indexOf('부품들의 공급업체는 다음과 같습니다')
    expect(graphIndex).toBeGreaterThan(-1)
    expect(answerIndex).toBeGreaterThan(-1)
    expect(graphIndex).toBeGreaterThan(answerIndex)
  })

  it('renders no graph block when hasGraphResult is false', () => {
    const html = renderToStaticMarkup(
      <NaturalLanguageAnswerBox answer="SQL로만 답한 질문입니다." hasGraphResult={false} />,
    )

    expect(html).not.toContain('그래프 렌더러를 불러오는 중입니다')
  })

  it('renders no graph block when the cypher result has only a single row', () => {
    const html = renderToStaticMarkup(
      <NaturalLanguageAnswerBox
        answer="부품 A의 공급업체는 Acme입니다."
        hasGraphResult
        graphRows={[{ supplierId: 1, supplierName: 'Acme' }]}
        graphError={null}
        graphEmptyReason={null}
      />,
    )

    expect(html).not.toContain('그래프 렌더러를 불러오는 중입니다')
  })
})
