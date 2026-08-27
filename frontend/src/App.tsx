import { useEffect, useState } from 'react'
import { BrowserRouter, Routes, Route } from 'react-router-dom'
import { Dashboard } from '@/screens/Dashboard'
import { LoginPage } from '@/pages/LoginPage'
import { ProtectedRoute } from '@/components/ProtectedRoute'
import { useUiStore } from '@/store/useUiStore'
import { useAuthStore } from '@/store/useAuthStore'
import { useHealthStore } from '@/store/useHealthStore'

const HEALTH_POLL_INTERVAL_MS = 30_000

// sessionStorage에서 UI 상태(테마/화면 단계 등)를 복원하는 게 끝났는지 추적한다.
// 끝나기 전엔 항상 기본값(theme: 'light', activeScreen: 'idle')이라, 이 값을
// 그대로 화면에 반영해버리면 복원된 값으로 다시 바뀌면서 깜빡인다.
function useUiHydrated() {
  const [hydrated, setHydrated] = useState(() => useUiStore.persist.hasHydrated())

  useEffect(() => {
    if (hydrated) return
    return useUiStore.persist.onFinishHydration(() => setHydrated(true))
  }, [hydrated])

  return hydrated
}

function App() {
  const theme = useUiStore((s) => s.theme)
  const uiHydrated = useUiHydrated()
  const checkAuth = useAuthStore((s) => s.checkAuth)
  const authStatus = useAuthStore((s) => s.status)
  const checkHealth = useHealthStore((s) => s.checkHealth)

  // 전역 테마 상태가 바뀔 때마다 <html>에 dark 클래스를 토글해 Tailwind 다크모드를 적용한다.
  // 복원이 끝나기 전의 기본값(light)으로 먼저 토글해버리면, index.html의 인라인
  // 스크립트가 미리 걸어둔 dark 클래스를 지웠다가 복원 후 다시 켜는 깜빡임이 생긴다.
  useEffect(() => {
    if (!uiHydrated) return
    document.documentElement.classList.toggle('dark', theme === 'dark')
  }, [theme, uiHydrated])

  // 앱 시작 시 쿠키 기반 세션이 유효한지 확인한다
  useEffect(() => {
    checkAuth()
  }, [checkAuth])

  // TopBar의 Neo4j 연결 배지가 실제 상태를 반영하도록 주기적으로 확인한다
  useEffect(() => {
    checkHealth()
    const interval = setInterval(checkHealth, HEALTH_POLL_INTERVAL_MS)
    return () => clearInterval(interval)
  }, [checkHealth])

  // UI 상태 복원과 세션 확인이 둘 다 끝나기 전엔 화면을 그리지 않는다 - "idle 화면 →
  // 로딩 문구 → 실제 화면"처럼 여러 번 바뀌는 대신 한 번만 자연스럽게 나타나게 한다.
  if (!uiHydrated || authStatus === 'idle' || authStatus === 'loading') {
    return <div className="h-screen bg-bg" />
  }

  return (
    <BrowserRouter>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route
          path="/"
          element={
            <ProtectedRoute>
              <Dashboard />
            </ProtectedRoute>
          }
        />
      </Routes>
    </BrowserRouter>
  )
}

export default App
