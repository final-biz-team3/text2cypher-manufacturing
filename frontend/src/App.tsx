import { useEffect } from 'react'
import { Button } from '@/components/ui/button'
import { useUiStore } from '@/store/useUiStore'

function App() {
  const theme = useUiStore((s) => s.theme)
  const setTheme = useUiStore((s) => s.setTheme)

  useEffect(() => {
    document.documentElement.classList.toggle('dark', theme === 'dark')
  }, [theme])

  return (
    <div className="p-4">
      <Button onClick={() => setTheme(theme === 'light' ? 'dark' : 'light')}>{theme}</Button>
    </div>
  )
}

export default App
