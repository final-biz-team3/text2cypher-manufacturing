import { create } from 'zustand'
import type { DashboardOverview } from '@/lib/dashboard'

export interface EntitySelection {
  type: string
  id: string | number
  sourceLabel?: string
}

interface DashboardStore {
  overview: DashboardOverview | null
  selectedCardKey: string | null
  selectedEntity: EntitySelection | null
  setOverview: (overview: DashboardOverview | null) => void
  setSelectedCardKey: (key: string | null) => void
  setSelectedEntity: (entity: EntitySelection | null) => void
  resetPanels: () => void
}

export const useDashboardStore = create<DashboardStore>((set) => ({
  overview: null,
  selectedCardKey: null,
  selectedEntity: null,
  setOverview: (overview) => set({ overview }),
  setSelectedCardKey: (selectedCardKey) => set({ selectedCardKey, selectedEntity: null }),
  setSelectedEntity: (selectedEntity) => set({ selectedEntity }),
  resetPanels: () => set({ selectedCardKey: null, selectedEntity: null }),
}))
