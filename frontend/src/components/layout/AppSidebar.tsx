import type { ReactNode } from 'react'
import { LayoutDashboard, MessageSquareText } from 'lucide-react'

interface AppSidebarProps {
  activeSection: 'dashboard' | 'chat'
  onNavigateDashboard: () => void
  onNavigateChat: () => void
  children?: ReactNode
}

const NAV_ITEMS = [
  { key: 'chat', label: 'AI Chat', icon: MessageSquareText },
  { key: 'dashboard', label: '전체 현황', icon: LayoutDashboard },
] as const

export function AppSidebar({
  activeSection,
  onNavigateDashboard,
  onNavigateChat,
  children,
}: AppSidebarProps) {
  return (
    <aside className="flex w-16 shrink-0 flex-col overflow-hidden border-r border-border bg-panel sm:w-[240px]">
      <nav className="shrink-0 border-b border-border p-2 sm:p-3" aria-label="주요 화면">
        <p className="mb-2 hidden px-2 text-[10px] font-semibold uppercase tracking-[0.12em] text-text-faint sm:block">
          Workspace
        </p>
        <div className="flex flex-col gap-1">
          {NAV_ITEMS.map((item) => {
            const Icon = item.icon
            const selected = activeSection === item.key
            return (
              <button
                key={item.key}
                type="button"
                title={item.label}
                aria-current={selected ? 'page' : undefined}
                aria-label={item.label}
                onClick={item.key === 'chat' ? onNavigateChat : onNavigateDashboard}
                className={`flex h-10 w-full items-center justify-center gap-2.5 rounded-md px-2 text-[12.5px] font-semibold transition-colors sm:justify-start ${
                  selected
                    ? 'bg-info text-white shadow-sm'
                    : 'text-text-muted hover:bg-panel-2 hover:text-text'
                }`}
              >
                <Icon className="size-4 shrink-0" aria-hidden="true" />
                <span className="hidden sm:inline">{item.label}</span>
              </button>
            )
          })}
        </div>
      </nav>
      {children ? <div className="hidden min-h-0 flex-1 flex-col sm:flex">{children}</div> : null}
    </aside>
  )
}
