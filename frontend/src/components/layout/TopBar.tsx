import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { useUiStore } from '@/store/useUiStore'

interface TopBarProps {
  connected: boolean
  connectionEndpoint: string
  readOnly: boolean
  onNavigateHome: () => void
}

export function TopBar({ connected, connectionEndpoint, readOnly, onNavigateHome }: TopBarProps) {
  const theme = useUiStore((s) => s.theme)
  const setTheme = useUiStore((s) => s.setTheme)

  return (
    <header className="flex h-14 shrink-0 items-center justify-between border-b border-border bg-panel px-4">
      <button type="button" onClick={onNavigateHome} className="flex items-baseline gap-2 text-left">
        <span className="text-[15px] font-bold text-text">공정 지식그래프 어시스턴트</span>
        <span className="text-xs text-text-muted">품질 분석 · Neo4j 지식그래프</span>
      </button>
      <div className="flex items-center gap-2">
        <Badge
          variant="outline"
          className="gap-1.5 rounded-full border-border-strong px-3 py-1 text-[11.5px] font-normal text-text"
        >
          <span className={`size-1.5 rounded-full ${connected ? 'bg-success' : 'bg-fail'}`} />
          {connected ? `Neo4j 연결됨 · ${connectionEndpoint}` : 'Neo4j 연결 안됨'}
        </Badge>
        {connected ? (
          <Badge
            variant="outline"
            className="rounded-full border-border-strong px-3 py-1 text-[11.5px] font-normal text-text"
          >
            {readOnly ? 'READ 전용 · 쓰기 작업 차단됨' : '쓰기 가능'}
          </Badge>
        ) : null}
        <Button
          type="button"
          variant="ghost"
          size="sm"
          onClick={() => setTheme(theme === 'light' ? 'dark' : 'light')}
        >
          {theme === 'light' ? '다크모드' : '라이트모드'}
        </Button>
      </div>
    </header>
  )
}
