import { create } from 'zustand'
import { login as apiLogin, logout as apiLogout } from '@/lib/api'
import type { CurrentUser } from '@/lib/schemas'

export type AuthStatus = 'authenticated' | 'unauthenticated'

interface AuthStore {
  user: CurrentUser | null
  status: AuthStatus
  login: (username: string, password: string) => Promise<void>
  logout: () => Promise<void>
}

// 로그인 사용자 정보와 인증 상태를 관리한다. 세션 쿠키가 남아 있어도
// 앱을 새로 열면 항상 로그아웃 상태로 시작한다(자동 로그인 복원 없음).
export const useAuthStore = create<AuthStore>((set) => ({
  user: null,
  status: 'unauthenticated',
  login: async (username, password) => {
    const user = await apiLogin({ username, password })
    set({ user, status: 'authenticated' })
  },
  logout: async () => {
    await apiLogout()
    set({ user: null, status: 'unauthenticated' })
  },
}))
