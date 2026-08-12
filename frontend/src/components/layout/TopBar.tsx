import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { useUiStore } from '@/store/useUiStore'

interface TopBarProps {
  connectionLabel: string
  readOnlyLabel: string
}

export function TopBar({ connectionLabel, readOnlyLabel }: TopBarProps) {
  const theme = useUiStore((s) => s.theme)
  const setTheme = useUiStore((s) => s.setTheme)

  return (
    <header className="flex h-14 shrink-0 items-center justify-between border-b border-border bg-panel px-4">
      <div className="flex items-baseline gap-2">
        <span className="text-[15px] font-bold text-text">공정 지식그래프 어시스턴트</span>
        <span className="text-xs text-text-muted">품질 분석 · Neo4j 지식그래프</span>
      </div>
      <div className="flex items-center gap-2">
        <Badge
          variant="outline"
          className="gap-1.5 rounded-full border-border-strong px-3 py-1 text-[11.5px] font-normal text-text"
        >
          <span className="size-1.5 rounded-full bg-success" />
          {connectionLabel}
        </Badge>
        <Badge
          variant="outline"
          className="rounded-full border-border-strong px-3 py-1 text-[11.5px] font-normal text-text"
        >
          {readOnlyLabel}
        </Badge>
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
