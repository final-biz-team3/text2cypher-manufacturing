import { z } from 'zod'
import { api } from './api'

const DashboardKpiSchema = z.object({
  key: z.string(),
  label: z.string(),
  value: z.number().nullable(),
  unit: z.string(),
  status: z.enum(['ready', 'error']),
})

const DashboardCardSchema = z.object({
  key: z.string(),
  title: z.string(),
  kind: z.literal('table'),
  status: z.enum(['ready', 'error']),
  columns: z.array(z.string()),
  sortableColumns: z.array(z.string()).optional(),
  rows: z.array(z.record(z.string(), z.unknown())),
  total: z.number(),
  page: z.number().optional(),
  pageSize: z.number().optional(),
  sort: z.string().optional(),
  direction: z.enum(['asc', 'desc']).optional(),
  entityType: z.string().optional(),
  entityIdField: z.string().optional(),
})

export const DashboardOverviewSchema = z.object({
  snapshot: z.object({
    syncRunId: z.string(),
    label: z.string(),
    scope: z.string(),
    syncedAt: z.string(),
    bomAsOfDate: z.string(),
  }),
  kpis: z.array(DashboardKpiSchema),
  cards: z.array(DashboardCardSchema),
  errors: z.array(z.object({ key: z.string(), code: z.string(), message: z.string() })),
})

export const ProcessOverviewSchema = z.object({
  availableRange: z.object({ from: z.string(), to: z.string() }),
  period: z.object({
    from: z.string(),
    to: z.string(),
    granularity: z.enum(['day', 'month']),
  }),
  kpis: z.array(DashboardKpiSchema),
  trend: z.array(
    z.object({
      date: z.string(),
      startedWorkOrderCount: z.number(),
      completedWorkOrderCount: z.number(),
      scrappedQty: z.number(),
    }),
  ),
  locations: z.array(
    z.object({
      locationId: z.number(),
      locationName: z.string(),
      operationCount: z.number(),
      workOrderCount: z.number(),
    }),
  ),
  errors: z.array(z.object({ key: z.string(), code: z.string(), message: z.string() })),
})

const EntityFieldSchema = z.object({
  key: z.string(),
  label: z.string(),
  value: z.unknown(),
})

export const EntityDetailSchema = z.object({
  entity: z.object({
    type: z.string(),
    id: z.union([z.string(), z.number()]),
    label: z.string(),
  }),
  groups: z.array(
    z.object({
      title: z.string(),
      fields: z.array(EntityFieldSchema),
    }),
  ),
  actions: z.array(
    z.object({ type: z.literal('chat-draft'), label: z.string(), question: z.string() }),
  ),
})

export const EntityNeighborsSchema = z.object({
  nodes: z.array(z.record(z.string(), z.unknown())),
  edges: z.array(z.record(z.string(), z.unknown())),
  truncated: z.boolean(),
})

export type DashboardOverview = z.infer<typeof DashboardOverviewSchema>
export type DashboardCard = z.infer<typeof DashboardCardSchema>
export type DashboardKpi = z.infer<typeof DashboardKpiSchema>
export type ProcessOverview = z.infer<typeof ProcessOverviewSchema>
export type EntityDetail = z.infer<typeof EntityDetailSchema>

export async function fetchDashboardOverview(signal?: AbortSignal): Promise<DashboardOverview> {
  const response = await api.get('/dashboard/overview', { signal })
  return DashboardOverviewSchema.parse(response.data)
}

export async function fetchProcessOverview(
  period: { from?: string; to?: string } = {},
  signal?: AbortSignal,
): Promise<ProcessOverview> {
  const response = await api.get('/dashboard/process-overview', { params: period, signal })
  return ProcessOverviewSchema.parse(response.data)
}

export async function fetchDashboardCard(
  cardKey: string,
  options: { page?: number; pageSize?: number; sort?: string; direction?: 'asc' | 'desc' } = {},
  signal?: AbortSignal,
): Promise<DashboardCard> {
  const response = await api.get(`/dashboard/cards/${encodeURIComponent(cardKey)}`, {
    params: options,
    signal,
  })
  return DashboardCardSchema.parse(response.data)
}

export async function fetchEntityDetail(
  entityType: string,
  entityId: string | number,
  signal?: AbortSignal,
): Promise<EntityDetail> {
  const response = await api.get(
    `/entities/${encodeURIComponent(entityType)}/${encodeURIComponent(String(entityId))}`,
    { signal },
  )
  return EntityDetailSchema.parse(response.data)
}

export async function fetchEntityNeighbors(
  entityType: string,
  entityId: string | number,
  signal?: AbortSignal,
) {
  const response = await api.get(
    `/entities/${encodeURIComponent(entityType)}/${encodeURIComponent(String(entityId))}/neighbors`,
    { params: { depth: 1 }, signal },
  )
  return EntityNeighborsSchema.parse(response.data)
}
