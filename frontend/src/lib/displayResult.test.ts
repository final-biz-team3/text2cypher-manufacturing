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
})

describe('final-result contract', () => {
  it('uses final rows and units instead of source rows', () => {
    const result = toDisplayResult({
      query: 'synthetic',
      status: 'answered',
      sql_result: { result: [{ raw: 999 }], error: null, attempts: [], empty_reason: null },
      result: {
        truncated: false,
        sections: [
          {
            id: 'final',
            title: 'Final',
            columns: [{ id: 'quantity', label: 'Quantity', value_type: 'number', unit: 'kg' }],
            rows: [{ quantity: 7 }, { quantity: null }],
            truncated: false,
          },
        ],
      },
    })
    expect(result.rows).toEqual([])
    expect(JSON.stringify(result.sections)).not.toContain('999')
    expect(JSON.stringify(result.sections)).toContain('7')
    expect(JSON.stringify(result.sections)).toContain('NULL')
    expect(JSON.stringify(result.sections)).toContain('kg')
  })
  it('does not present failed source rows as an empty answer', () => {
    const result = toDisplayResult({
      query: 'synthetic',
      status: 'unverified',
      result: null,
      sql_result: { result: [{ raw: 999 }], error: null, attempts: [], empty_reason: null },
    })
    expect(result.status).toBe('unverified')
    expect(result.rows).toEqual([])
    expect(result.sections).toEqual([])
  })
})
