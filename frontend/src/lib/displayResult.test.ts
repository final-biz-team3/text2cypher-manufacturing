import { describe, expect, it } from 'vitest'
import { toDisplayResult } from './displayResult'

describe('toDisplayResult', () => {
  it('keeps graph rows separate from display-table string conversion', () => {
    const graphRow = { productId: 680, productName: 'HL Road Frame' }
    const result = toDisplayResult({
      query: '제품 680을 보여줘',
      graph_result: {
        result: [graphRow],
        error: null,
        attempts: [],
        empty_reason: null,
      },
    })

    expect(result.hasGraphResult).toBe(true)
    expect(result.graphRows).toEqual([graphRow])
    expect(result.rows).toEqual([{ productId: '680', productName: 'HL Road Frame' }])
  })

  it('preserves graph error and empty-result states for the graph canvas', () => {
    const result = toDisplayResult({
      query: '그래프 결과가 없는 질문',
      graph_result: {
        result: [],
        error: 'GRAPH_QUERY_FAILED',
        attempts: [],
        empty_reason: 'INCONCLUSIVE',
      },
    })

    expect(result).toMatchObject({
      hasGraphResult: true,
      graphRows: [],
      graphError: 'GRAPH_QUERY_FAILED',
      graphEmptyReason: 'INCONCLUSIVE',
    })
  })

  it('does not mount a graph for a response without graph_result', () => {
    const result = toDisplayResult({ query: 'SQL 전용 질문' })

    expect(result.hasGraphResult).toBe(false)
    expect(result.graphRows).toEqual([])
  })

  it('does not present legacy COMPOSED dumps as an AI answer', () => {
    const result = toDisplayResult({
      query: '재고가 부족한 제품을 알려줘',
      final_answer: "COMPOSED: {'mode': 'single', 'rows': [{'productId': 680}]}",
    })

    expect(result.answer).not.toContain('COMPOSED:')
    expect(result.answer).toContain('현재 LLM')
  })

  it.each(['GRAPH:', 'SQL:'])('does not present legacy %s dumps as an AI answer', (prefix) => {
    const result = toDisplayResult({
      query: '조회 질문',
      final_answer: `${prefix} {'result': [{'productId': 680}]}`,
    })

    expect(result.answer).not.toContain(prefix)
    expect(result.answer).toContain('현재 LLM')
  })

  it('passes visualization spec through from a chat response', () => {
    const result = toDisplayResult({
      query: '판매량이 가장 많은 완제품 상위 3개를 알려줘.',
      visualization: {
        type: 'bar',
        title: null,
        categoryLabel: '제품명',
        series: [{ key: 'value', label: '판매량' }],
        data: [{ category: 'Product A', value: 8420 }],
      },
    })

    expect(result.visualization).toEqual({
      type: 'bar',
      title: null,
      categoryLabel: '제품명',
      series: [{ key: 'value', label: '판매량' }],
      data: [{ category: 'Product A', value: 8420 }],
    })
  })

  it('defaults visualization to null when the response omits it', () => {
    const result = toDisplayResult({ query: '단순 질문' })

    expect(result.visualization).toBeNull()
  })
})
