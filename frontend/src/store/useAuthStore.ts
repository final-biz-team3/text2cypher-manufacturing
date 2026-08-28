import { create } from 'zustand'
import type { AxiosError } from 'axios'
import { login as apiLogin, logout as apiLogout, fetchMe } from '@/lib/api'
import type { CurrentUser } from '@/lib/schemas'
import { useUiStore } from '@/store/useUiStore'

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
    // 이전 계정(혹은 이전 세션)이 보던 질문 결과 화면이 남아있지 않도록 초기화한다
    useUiStore.getState().resetSession()
  },
  logout: async () => {
    await apiLogout()
    set({ user: null, status: 'unauthenticated' })
    useUiStore.getState().resetSession()
  },
  checkAuth: async () => {
    set({ status: 'loading' })
    try {
      const user = await fetchMe()
      set({ user, status: 'authenticated' })
    } catch (err) {
      // 401(비로그인)은 정상적인 경우라 로그를 남기지 않는다 - 네트워크 오류·5xx처럼
      // 진짜 문제일 때만 콘솔에 남겨서 실제 장애를 눈에 띄게 한다.
      const axiosErr = err as AxiosError
      if (axiosErr.response?.status !== 401) {
        console.error('checkAuth failed:', err)
      }
      set({ user: null, status: 'unauthenticated' })
    }
  },
}))
