import { create } from 'zustand'
import { createJSONStorage, persist } from 'zustand/middleware'
import type { DisplayResult } from '@/types/query'

// 여러 컴포넌트가 공유하는 전역 UI 상태(테마, 대화 턴 목록, 패널 열림/접힘 등)
export type Theme = 'light' | 'dark'
export type TurnStatus = 'loading' | 'success' | 'error' | 'clarify'
export type SidebarTab = 'schema' | 'history'

// 대화창의 한 턴(질문 1개 + 그 처리 상태/결과). 후보 선택으로 재질의하는 동안에도
// 같은 턴을 그대로 갱신해 대화 목록에 새 항목이 중복으로 쌓이지 않게 한다.
export interface ConversationTurn {
  id: string
  query: string
  status: TurnStatus
  result: DisplayResult | null
  errorMessage: string
}

interface UiStore {
  theme: Theme
  turns: ConversationTurn[]
  queryPanelCollapsed: boolean
  historyTab: SidebarTab
  setTheme: (theme: Theme) => void
  addTurn: (turn: ConversationTurn) => void
  updateTurn: (id: string, patch: Partial<ConversationTurn>) => void
  removeTurn: (id: string) => void
  clearTurns: () => void
  toggleQueryPanelCollapsed: () => void
  setHistoryTab: (tab: SidebarTab) => void
  resetSession: () => void
}

// 새로고침해도 사용자가 보던 대화가 그대로 유지되도록 turns를 sessionStorage에
// 저장한다(탭을 닫으면 사라짐 - 브라우저를 껐다 켜도 지난 대화가 그대로
// 남아있는 건 오히려 어색해서 localStorage 대신 세션 스토리지를 쓴다).
// 입력창 텍스트(queryText)는 여기 안 둔다 - Dashboard.tsx 참고.
export const useUiStore = create<UiStore>()(
  persist(
    (set) => ({
      theme: 'light',
      turns: [],
      queryPanelCollapsed: false,
      historyTab: 'schema',
      setTheme: (theme) => set({ theme }),
      addTurn: (turn) => set((s) => ({ turns: [...s.turns, turn] })),
      updateTurn: (id, patch) =>
        set((s) => ({
          turns: s.turns.map((t) => (t.id === id ? { ...t, ...patch } : t)),
        })),
      removeTurn: (id) => set((s) => ({ turns: s.turns.filter((t) => t.id !== id) })),
      clearTurns: () => set({ turns: [] }),
      toggleQueryPanelCollapsed: () =>
        set((s) => ({ queryPanelCollapsed: !s.queryPanelCollapsed })),
      setHistoryTab: (historyTab) => set({ historyTab }),
      // 로그인/로그아웃 시 이전 계정(혹은 이전 세션)의 대화가 그대로 남아있지
      // 않도록 초기화한다. useAuthStore의 login/logout에서 호출한다.
      resetSession: () => set({ turns: [] }),
    }),
    {
      name: 'kg-ui-state',
      storage: createJSONStorage(() => sessionStorage),
      partialize: (state) => ({
        theme: state.theme,
        turns: state.turns,
      }),
      // 요청이 날아가던 도중 새로고침했다면 그 요청은 이미 사라진 것이라
      // "답변을 생성하는 중입니다…" 상태가 영원히 멈춰있게 된다 - 에러로 되돌린다.
      // clarify 턴의 후보 목록은 컴포넌트 로컬 상태라 세션에 저장되지 않으므로
      // 새로고침하면 함께 사라지고, 후보를 고를 방법이 없어지므로 역시 에러로 되돌린다.
      onRehydrateStorage: () => (state) => {
        if (!state) return
        state.turns = state.turns.map((t) =>
          t.status === 'loading' || t.status === 'clarify'
            ? {
                ...t,
                status: 'error',
                errorMessage: '새로고침으로 중단된 질문입니다. 다시 질문해 주세요.',
              }
            : t,
        )
      },
    },
  ),
)
