import { useEffect } from 'react'
import { TopBar } from '@/components/layout/TopBar'
import { SchemaSidebar } from '@/components/layout/SchemaSidebar'
import { useUiStore } from '@/store/useUiStore'

function App() {
  const theme = useUiStore((s) => s.theme)

  useEffect(() => {
    document.documentElement.classList.toggle('dark', theme === 'dark')
  }, [theme])

  return (
    <div className="flex h-screen flex-col bg-bg">
      <TopBar />
      <div className="flex flex-1 overflow-hidden">
        <SchemaSidebar />
      </div>
    </div>
  )
}

export default App
