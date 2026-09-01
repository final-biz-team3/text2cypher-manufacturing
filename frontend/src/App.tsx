import { useEffect } from 'react'
import { BrowserRouter, Navigate, Routes, Route } from 'react-router-dom'
import { Dashboard } from '@/screens/Dashboard'
import { OverviewDashboard } from '@/screens/OverviewDashboard'
import { LoginPage } from '@/pages/LoginPage'
import { ProtectedRoute } from '@/components/ProtectedRoute'
import { useUiStore } from '@/store/useUiStore'
import { useAuthStore } from '@/store/useAuthStore'
import { useHealthStore } from '@/store/useHealthStore'

const HEALTH_POLL_INTERVAL_MS = 30_000

function App() {
  const theme = useUiStore((s) => s.theme)
  const checkAuth = useAuthStore((s) => s.checkAuth)
  const authStatus = useAuthStore((s) => s.status)
  const checkHealth = useHealthStore((s) => s.checkHealth)

  // 전역 테마 상태가 바뀔 때마다 <html>에 dark 클래스를 토글해 Tailwind 다크모드를 적용한다
  useEffect(() => {
    document.documentElement.classList.toggle('dark', theme === 'dark')
  }, [theme])

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

  if (authStatus === 'idle' || authStatus === 'loading') {
    return <div className="h-screen bg-bg" />
  }

  return (
    <BrowserRouter>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route path="/" element={<Navigate to="/dashboard" replace />} />
        <Route
          path="/dashboard"
          element={
            <ProtectedRoute>
              <OverviewDashboard />
            </ProtectedRoute>
          }
        />
        <Route
          path="/chat"
          element={
            <ProtectedRoute>
              <Dashboard />
            </ProtectedRoute>
          }
        />
        <Route path="*" element={<Navigate to="/dashboard" replace />} />
      </Routes>
    </BrowserRouter>
  )
}

export default App
