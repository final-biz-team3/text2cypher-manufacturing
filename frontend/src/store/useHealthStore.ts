import { create } from 'zustand'
import { fetchHealth } from '@/lib/api'

interface HealthStore {
  neo4jConnected: boolean
  postgresConnected: boolean
  checkHealth: () => Promise<void>
}

// /health를 조회해 Neo4j 연결 상태를 TopBar 배지에 반영한다
export const useHealthStore = create<HealthStore>((set) => ({
  neo4jConnected: false,
  postgresConnected: false,
  checkHealth: async () => {
    try {
      const health = await fetchHealth()
      set({
        neo4jConnected: health.neo4j.status === 'ok',
        postgresConnected: health.postgres.status === 'ok',
      })
    } catch (err) {
      console.error('checkHealth failed:', err)
      set({ neo4jConnected: false, postgresConnected: false })
    }
  },
}))
