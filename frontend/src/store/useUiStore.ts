import { create } from 'zustand'

// 여러 컴포넌트가 공유하는 전역 UI 상태(테마, 화면 단계, 패널 열림/접힘 등)
export type Theme = 'light' | 'dark'
export type ActiveScreen = 'idle' | 'loading' | 'success' | 'error'
export type SidebarTab = 'schema' | 'history'

interface UiStore {
  theme: Theme
  activeScreen: ActiveScreen
  evidencePanelOpen: boolean
  cypherCollapsed: boolean
  historyTab: SidebarTab
  setTheme: (theme: Theme) => void
  setActiveScreen: (screen: ActiveScreen) => void
  toggleEvidencePanel: () => void
  toggleCypherCollapsed: () => void
  setHistoryTab: (tab: SidebarTab) => void
}

export const useUiStore = create<UiStore>((set) => ({
  theme: 'light',
  activeScreen: 'idle',
  evidencePanelOpen: false,
  cypherCollapsed: false,
  historyTab: 'schema',
  setTheme: (theme) => set({ theme }),
  setActiveScreen: (activeScreen) => set({ activeScreen }),
  toggleEvidencePanel: () => set((s) => ({ evidencePanelOpen: !s.evidencePanelOpen })),
  toggleCypherCollapsed: () => set((s) => ({ cypherCollapsed: !s.cypherCollapsed })),
  setHistoryTab: (historyTab) => set({ historyTab }),
}))
