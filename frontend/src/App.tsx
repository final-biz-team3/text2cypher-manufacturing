import { useEffect } from 'react'
import { BrowserRouter, Routes, Route } from 'react-router-dom'
import { Dashboard } from '@/screens/Dashboard'
import { LoginPage } from '@/pages/LoginPage'
import { ProtectedRoute } from '@/components/ProtectedRoute'
import { useUiStore } from '@/store/useUiStore'
import { useAuthStore } from '@/store/useAuthStore'

function App() {
  const theme = useUiStore((s) => s.theme)
  const checkAuth = useAuthStore((s) => s.checkAuth)

  // 전역 테마 상태가 바뀔 때마다 <html>에 dark 클래스를 토글해 Tailwind 다크모드를 적용한다
  useEffect(() => {
    document.documentElement.classList.toggle('dark', theme === 'dark')
  }, [theme])

  // 앱 시작 시 쿠키 기반 세션이 유효한지 확인한다
  useEffect(() => {
    checkAuth()
  }, [checkAuth])

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
