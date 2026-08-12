import { useEffect } from 'react'
import { Dashboard } from '@/screens/Dashboard'
import { useUiStore } from '@/store/useUiStore'

function App() {
  const theme = useUiStore((s) => s.theme)

  useEffect(() => {
    document.documentElement.classList.toggle('dark', theme === 'dark')
  }, [theme])

  return <Dashboard />
}

export default App
