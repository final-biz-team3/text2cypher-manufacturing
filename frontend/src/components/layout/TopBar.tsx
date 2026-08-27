import { Home, User } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { useUiStore } from '@/store/useUiStore'

interface TopBarProps {
  connected: boolean
  readOnly: boolean
  onNavigateHome: () => void
  username?: string
  onLogout?: () => void
}

// 상단 헤더: 서비스명(홈 이동 버튼), Neo4j 연결/읽기전용 상태 배지, 다크모드 토글, 로그인 사용자·로그아웃을 담당한다
export function TopBar({ connected, readOnly, onNavigateHome, username, onLogout }: TopBarProps) {
  const theme = useUiStore((s) => s.theme)
  const setTheme = useUiStore((s) => s.setTheme)

  return (
    <header className="flex h-14 shrink-0 items-center justify-between border-b border-border bg-panel px-4">
      <button
        type="button"
        onClick={onNavigateHome}
        title="홈으로 이동"
        className="flex cursor-pointer items-center gap-2 rounded-md px-2 py-1 text-left transition-colors hover:bg-panel-2 active:bg-border"
      >
        <Home className="size-4 text-text-muted" />
        <span className="flex items-baseline gap-2">
          <span className="text-[15px] font-bold text-text">제조 데이터 어시스턴트</span>
          <span className="text-xs text-text-muted">
            제품·재고·공급망 조회 · SQL + Neo4j 지식그래프
          </span>
        </span>
      </button>
      <div className="flex items-center gap-2">
        <Badge
          variant="outline"
          className="gap-1.5 rounded-full border-border-strong px-3 py-1 text-[11.5px] font-normal text-text"
        >
          <span className={`size-1.5 rounded-full ${connected ? 'bg-success' : 'bg-fail'}`} />
          {connected ? 'Neo4j 연결됨' : 'Neo4j 연결 안됨'}
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
          variant="outline"
          size="sm"
          className="rounded-full border-border-strong bg-transparent hover:bg-panel-2"
          onClick={() => setTheme(theme === 'light' ? 'dark' : 'light')}
        >
          {theme === 'light' ? '다크모드' : '라이트모드'}
        </Button>
        {username ? (
          <span className="flex items-center gap-1.5 rounded-full border border-border-strong px-3 py-1 text-[11.5px] font-medium text-text">
            <User className="size-3.5" />
            {username}
          </span>
        ) : null}
        {onLogout ? (
          <Button type="button" variant="ghost" size="sm" onClick={onLogout}>
            로그아웃
          </Button>
        ) : null}
      </div>
    </header>
  )
}
