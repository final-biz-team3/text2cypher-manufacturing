import { create } from 'zustand'
import { createJSONStorage, persist } from 'zustand/middleware'
import type { DisplayResult } from '@/types/query'

// 여러 컴포넌트가 공유하는 전역 UI 상태(테마, 화면 단계, 패널 열림/접힘 등)
export type Theme = 'light' | 'dark'
export type ActiveScreen = 'idle' | 'loading' | 'success' | 'error'
export type SidebarTab = 'schema' | 'history'

interface UiStore {
  theme: Theme
  activeScreen: ActiveScreen
  evidencePanelOpen: boolean
  queryPanelCollapsed: boolean
  historyTab: SidebarTab
  result: DisplayResult | null
  errorMessage: string
  setTheme: (theme: Theme) => void
  setActiveScreen: (screen: ActiveScreen) => void
  toggleEvidencePanel: () => void
  toggleQueryPanelCollapsed: () => void
  setHistoryTab: (tab: SidebarTab) => void
  setResult: (result: DisplayResult | null) => void
  setErrorMessage: (message: string) => void
  resetSession: () => void
}

// 새로고침해도 사용자가 보던 화면 그대로 유지되도록 activeScreen/결과를
// sessionStorage에 저장한다(탭을 닫으면 사라짐 - 브라우저를 껐다 켜도 지난
// 대화가 그대로 남아있는 건 오히려 어색해서 localStorage 대신 세션 스토리지를 쓴다).
// 입력창 텍스트(queryText)는 여기 안 둔다 - Dashboard.tsx 참고.
export const useUiStore = create<UiStore>()(
  persist(
    (set) => ({
      theme: 'light',
      activeScreen: 'idle',
      evidencePanelOpen: false,
      queryPanelCollapsed: false,
      historyTab: 'schema',
      result: null,
      errorMessage: '',
      setTheme: (theme) => set({ theme }),
      setActiveScreen: (activeScreen) => set({ activeScreen }),
      toggleEvidencePanel: () => set((s) => ({ evidencePanelOpen: !s.evidencePanelOpen })),
      toggleQueryPanelCollapsed: () =>
        set((s) => ({ queryPanelCollapsed: !s.queryPanelCollapsed })),
      setHistoryTab: (historyTab) => set({ historyTab }),
      setResult: (result) => set({ result }),
      setErrorMessage: (errorMessage) => set({ errorMessage }),
      // 로그인/로그아웃 시 이전 계정(혹은 이전 세션)의 질문 결과 화면이 그대로
      // 남아있지 않도록 초기화한다. useAuthStore의 login/logout에서 호출한다.
      resetSession: () => set({ activeScreen: 'idle', result: null, errorMessage: '' }),
    }),
    {
      name: 'kg-ui-state',
      storage: createJSONStorage(() => sessionStorage),
      partialize: (state) => ({
        theme: state.theme,
        activeScreen: state.activeScreen,
        result: state.result,
        errorMessage: state.errorMessage,
      }),
      // 요청이 날아가던 도중 새로고침했다면 그 요청은 이미 사라진 것이라
      // "답변을 생성하는 중입니다…" 화면이 영원히 멈춰있게 된다 - idle로 되돌린다.
      onRehydrateStorage: () => (state) => {
        if (state?.activeScreen === 'loading') {
          state.activeScreen = 'idle'
        }
      },
    },
  ),
)
