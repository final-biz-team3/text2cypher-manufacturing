import { describe, expect, it } from 'vitest'
import { entityFromRow, formatDashboardValue, formatSnapshotDateTime } from './dashboardFormatters'

describe('dashboard formatters', () => {
  it('formats null and boolean fields consistently', () => {
    expect(formatDashboardValue('color', null)).toBe('미등록')
    expect(formatDashboardValue('makeFlag', false)).toBe('외부 구매')
    expect(formatDashboardValue('active', true)).toBe('활성')
  })

  it('resolves supported entity identifiers in deterministic priority order', () => {
    expect(entityFromRow({ productId: 10 })).toEqual({ type: 'product', id: 10 })
    expect(entityFromRow({ workOrderId: 2, productId: 10 })).toEqual({
      type: 'work-order',
      id: 2,
    })
    expect(entityFromRow({ categoryId: 1 })).toBeNull()
  })

  it('formats the snapshot sync time in Korea time and guards invalid values', () => {
    expect(formatSnapshotDateTime('2026-08-22T01:52:45Z')).toContain('2026. 8. 22.')
    expect(formatSnapshotDateTime('')).toBe('미등록')
  })
})
