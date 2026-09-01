import { describe, expect, it } from 'vitest'
import { createGraphNodePresentation } from './graphNodePresentation'

describe('graph node presentation', () => {
  it('summarizes product identity and stock before selection', () => {
    expect(
      createGraphNodePresentation('Product', 'HL Road Frame', {
        productId: 680,
        productNumber: 'FR-R92B-58',
        actualStock: 0,
      }),
    ).toEqual({
      categoryLabel: '제품',
      displayTitle: 'HL Road Frame',
      displayMeta: '제품번호 FR-R92B-58 · 재고 0개',
    })
  })

  it('summarizes work order risk values', () => {
    expect(
      createGraphNodePresentation('WorkOrder', 'WorkOrder 123', {
        workOrderId: 123,
        scrappedQty: 7,
      }).displayMeta,
    ).toBe('ID 123 · 폐기 7개')
  })

  it('uses localized category names with a safe fallback', () => {
    expect(
      createGraphNodePresentation('Location', 'Subassembly', { locationId: 50 }),
    ).toMatchObject({ categoryLabel: '작업장', displayMeta: 'ID 50' })
    expect(createGraphNodePresentation('Custom', 'Custom node', { id: 'A-1' })).toMatchObject({
      categoryLabel: 'Custom',
      displayMeta: 'ID A-1',
    })
  })
})
