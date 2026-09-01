import { Database, Moon, Sun, User } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { useUiStore } from '@/store/useUiStore'

interface TopBarProps {
  connected: boolean
  readOnly: boolean
  onNavigateHome: () => void
  username?: string
  onLogout?: () => void
  postgresConnected?: boolean
  snapshotLabel?: string
}

// 상단 헤더: 서비스명(홈 이동 버튼), Neo4j 연결/읽기전용 상태 배지, 다크모드 토글, 로그인 사용자·로그아웃을 담당한다
export function TopBar({
  connected,
  readOnly,
  onNavigateHome,
  username,
  onLogout,
  postgresConnected = false,
  snapshotLabel,
}: TopBarProps) {
  const theme = useUiStore((s) => s.theme)
  const setTheme = useUiStore((s) => s.setTheme)

  return (
    <header className="flex h-14 shrink-0 items-center justify-between gap-3 border-b border-border bg-panel px-4 lg:px-6">
      <div className="flex min-w-0 items-center gap-4">
        <button
          type="button"
          onClick={onNavigateHome}
          title="홈으로 이동"
          className="flex shrink-0 cursor-pointer items-center gap-2 rounded-md px-1 py-1 text-left transition-colors hover:bg-panel-2 active:bg-border"
        >
          <span className="flex size-7 items-center justify-center rounded-md bg-info text-[11px] font-black tracking-tight text-white">
            IT
          </span>
          <span className="hidden text-[14px] font-bold text-text sm:inline">
            제조 데이터 어시스턴트
          </span>
        </button>
      </div>
      <div className="flex min-w-0 items-center gap-2">
        {snapshotLabel ? (
          <span className="hidden max-w-64 truncate text-[11px] text-text-muted xl:inline">
            {snapshotLabel}
          </span>
        ) : null}
        <Badge
          variant="outline"
          className="hidden gap-1.5 rounded-full border-border-strong px-3 py-1 text-[11px] font-normal text-text lg:flex"
        >
          <Database className="size-3" />
          <span
            className={`size-1.5 rounded-full ${connected && postgresConnected ? 'bg-success' : 'bg-fail'}`}
          />
          {connected && postgresConnected ? 'DB 연결됨' : 'DB 연결 확인 필요'}
        </Badge>
        {connected && !username ? (
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
          aria-label={theme === 'light' ? '다크모드로 전환' : '라이트모드로 전환'}
          title={theme === 'light' ? '다크모드로 전환' : '라이트모드로 전환'}
          onClick={() => setTheme(theme === 'light' ? 'dark' : 'light')}
        >
          {theme === 'light' ? <Moon /> : <Sun />}
          <span className="hidden xl:inline">{theme === 'light' ? '다크모드' : '라이트모드'}</span>
        </Button>
        {username ? (
          <span className="hidden items-center gap-1.5 rounded-full border border-border-strong px-3 py-1 text-[11.5px] font-medium text-text sm:flex">
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
