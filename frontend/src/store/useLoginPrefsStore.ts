import { create } from 'zustand'
import { persist } from 'zustand/middleware'

interface LoginPrefsStore {
  rememberId: boolean
  savedUsername: string
  setRememberId: (value: boolean) => void
  setSavedUsername: (value: string) => void
}

// 로그인 화면의 "아이디 기억" 설정만 localStorage에 저장한다
export const useLoginPrefsStore = create<LoginPrefsStore>()(
  persist(
    (set) => ({
      rememberId: true,
      savedUsername: '',
      setRememberId: (rememberId) => set({ rememberId }),
      setSavedUsername: (savedUsername) => set({ savedUsername }),
    }),
    { name: 'kg-login-prefs' },
  ),
)
