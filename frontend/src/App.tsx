import { useEffect } from 'react'
import { Dashboard } from '@/screens/Dashboard'
import { useUiStore } from '@/store/useUiStore'

function App() {
  const theme = useUiStore((s) => s.theme)

  // 전역 테마 상태가 바뀔 때마다 <html>에 dark 클래스를 토글해 Tailwind 다크모드를 적용한다
  useEffect(() => {
    document.documentElement.classList.toggle('dark', theme === 'dark')
  }, [theme])

  return <Dashboard />
}

export default App
