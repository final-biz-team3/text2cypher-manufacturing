import { create } from 'zustand'
import { login as apiLogin, logout as apiLogout, fetchMe } from '@/lib/api'
import type { CurrentUser } from '@/lib/schemas'

export type AuthStatus = 'idle' | 'loading' | 'authenticated' | 'unauthenticated'

interface AuthStore {
  user: CurrentUser | null
  status: AuthStatus
  login: (username: string, password: string) => Promise<void>
  logout: () => Promise<void>
  checkAuth: () => Promise<void>
}

// 로그인 사용자 정보와 인증 상태를 관리한다
export const useAuthStore = create<AuthStore>((set) => ({
  user: null,
  status: 'idle',
  login: async (username, password) => {
    const user = await apiLogin({ username, password })
    set({ user, status: 'authenticated' })
  },
  logout: async () => {
    await apiLogout()
    set({ user: null, status: 'unauthenticated' })
  },
  checkAuth: async () => {
    set({ status: 'loading' })
    try {
      const user = await fetchMe()
      set({ user, status: 'authenticated' })
    } catch {
      set({ user: null, status: 'unauthenticated' })
    }
  },
}))
