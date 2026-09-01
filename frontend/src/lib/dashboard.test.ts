import { describe, expect, it } from 'vitest'
import { DashboardOverviewSchema, EntityDetailSchema } from './dashboard'

describe('DashboardOverviewSchema', () => {
  it('accepts partial failure without discarding ready cards', () => {
    const parsed = DashboardOverviewSchema.parse({
      snapshot: {
        syncRunId: 'sync-1',
        label: 'AdventureWorks 전체 데이터 스냅샷',
        scope: '특정 하루가 아닌 전체 스냅샷 집계',
        syncedAt: '2026-08-22T01:52:45Z',
        bomAsOfDate: '2014-08-08',
      },
      kpis: [
        { key: 'product_count', label: '전체 제품', value: null, unit: '개', status: 'error' },
      ],
      cards: [
        {
          key: 'low_stock_top5',
          title: '안전재고 미달 제품',
          kind: 'table',
          status: 'ready',
          columns: ['productId', 'shortageQty'],
          sortableColumns: ['shortageQty'],
          rows: [{ productId: 1, shortageQty: 4 }],
          total: 1,
        },
      ],
      errors: [{ key: 'product_count', code: 'DASHBOARD_QUERY_FAILED', message: '불러오기 실패' }],
    })

    expect(parsed.cards[0].rows).toHaveLength(1)
    expect(parsed.errors[0].key).toBe('product_count')
  })
})

describe('EntityDetailSchema', () => {
  it('preserves grouped list values and chat draft actions', () => {
    const parsed = EntityDetailSchema.parse({
      entity: { type: 'product', id: 956, label: 'Touring-1000 Yellow, 54' },
      groups: [
        {
          title: '재고 위치',
          fields: [
            {
              key: 'inventoryLocations',
              label: '위치별 재고',
              value: [{ locationId: 1, quantity: 8 }],
            },
          ],
        },
      ],
      actions: [
        {
          type: 'chat-draft',
          label: 'AI Chat에서 분석',
          question: '재고 위치와 수량을 알려줘.',
        },
      ],
    })

    expect(parsed.groups[0].fields[0].value).toEqual([{ locationId: 1, quantity: 8 }])
    expect(parsed.actions[0].question).toContain('재고')
  })
})
