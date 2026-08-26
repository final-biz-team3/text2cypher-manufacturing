import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { useUiStore } from '@/store/useUiStore'

interface TopBarProps {
  connected: boolean
  connectionEndpoint: string
  readOnly: boolean
  onNavigateHome: () => void
  username?: string
  onLogout?: () => void
}

// 상단 헤더: 서비스명(홈 이동 버튼), Neo4j 연결/읽기전용 상태 배지, 다크모드 토글, 로그인 사용자·로그아웃을 담당한다
export function TopBar({
  connected,
  connectionEndpoint,
  readOnly,
  onNavigateHome,
  username,
  onLogout,
}: TopBarProps) {
  const theme = useUiStore((s) => s.theme)
  const setTheme = useUiStore((s) => s.setTheme)

  return (
    <header className="flex h-14 shrink-0 items-center justify-between border-b border-border bg-panel px-4">
      <button
        type="button"
        onClick={onNavigateHome}
        className="flex cursor-pointer items-baseline gap-2 rounded-md px-1.5 py-1 text-left transition-colors hover:bg-panel-2 active:bg-border"
      >
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
        {username ? <span className="text-[11.5px] text-text-muted">{username}</span> : null}
        {onLogout ? (
          <Button type="button" variant="ghost" size="sm" onClick={onLogout}>
            로그아웃
          </Button>
        ) : null}
      </div>
    </header>
  )
}
