import { describe, expect, it } from 'vitest'
import {
  DashboardOverviewSchema,
  EntityDetailSchema,
  getProcessGranularityOptions,
  ProcessOverviewSchema,
  resolveProcessGranularity,
} from './dashboard'

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

describe('ProcessOverviewSchema', () => {
  it.each(['day', 'month', 'year'] as const)(
    'accepts %s period metrics, trend rows, and location rankings',
    (granularity) => {
      const parsed = ProcessOverviewSchema.parse({
        availableRange: { from: '2011-06-03', to: '2014-06-28' },
        period: { from: '2014-05-30', to: '2014-06-28', granularity },
        kpis: [
          { key: 'operationCount', label: '수행 공정', value: 10, unit: '건', status: 'ready' },
        ],
        trend: [
          {
            date: '2014-05-30',
            startedWorkOrderCount: 2,
            completedWorkOrderCount: 1,
            scrappedQty: 3,
          },
        ],
        locations: [
          { locationId: 10, locationName: 'Frame Forming', operationCount: 4, workOrderCount: 2 },
        ],
        errors: [],
      })

      expect(parsed.period.granularity).toBe(granularity)
      expect(parsed.trend[0].scrappedQty).toBe(3)
      expect(parsed.locations[0].operationCount).toBe(4)
    },
  )
})

describe('process granularity policy', () => {
  it.each([
    ['2014-06-01', '2014-06-07', ['day']],
    ['2014-04-01', '2014-06-28', ['day', 'month']],
    ['2014-01-01', '2014-06-28', ['month']],
    ['2011-06-03', '2014-06-28', ['month', 'year']],
  ] as const)('returns date-range options for %s through %s', (from, to, expected) => {
    expect(getProcessGranularityOptions(from, to)).toEqual(expected)
  })

  it('falls back when the current granularity does not fit the selected range', () => {
    expect(resolveProcessGranularity('year', '2014-06-01', '2014-06-28')).toBe('day')
    expect(resolveProcessGranularity('day', '2011-06-03', '2014-06-28')).toBe('month')
  })
})
