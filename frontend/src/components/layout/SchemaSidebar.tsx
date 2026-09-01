import { useState } from 'react'
import { X } from 'lucide-react'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { NodeGlyphBadge } from '@/components/common/NodeGlyphBadge'
import { AppSidebar } from '@/components/layout/AppSidebar'
import { useUiStore } from '@/store/useUiStore'
import type { SidebarTab } from '@/store/useUiStore'
import type { HistoryEntry } from '@/lib/schemas'
import type { SchemaNode, SchemaRelationship } from '@/types/query'

interface SchemaSidebarProps {
  nodes: SchemaNode[]
  relationships: SchemaRelationship[]
  history: HistoryEntry[]
  onSelectHistoryItem: (item: HistoryEntry) => void
  onDeleteHistoryItem: (item: HistoryEntry) => void
  onNavigateDashboard: () => void
  onNavigateChat: () => void
}

// 좌측 사이드바: "스키마"(노드/관계 타입 설명)와 "질문 이력" 두 탭을 전환하며 보여준다
export function SchemaSidebar({
  nodes,
  relationships,
  history,
  onSelectHistoryItem,
  onDeleteHistoryItem,
  onNavigateDashboard,
  onNavigateChat,
}: SchemaSidebarProps) {
  const historyTab = useUiStore((s) => s.historyTab)
  const setHistoryTab = useUiStore((s) => s.setHistoryTab)
  // 스키마 탭에서 펼쳐진 노드 아코디언은 이 컴포넌트 로컬 상태로만 관리한다(다른 컴포넌트와 공유 불필요)
  const [openNode, setOpenNode] = useState<string | null>(null)

  return (
    <AppSidebar
      activeSection="chat"
      onNavigateDashboard={onNavigateDashboard}
      onNavigateChat={onNavigateChat}
    >
      <Tabs
        value={historyTab}
        onValueChange={(v) => setHistoryTab(v as SidebarTab)}
        className="flex min-h-0 flex-1 flex-col"
      >
        <TabsList variant="line" className="w-full px-2 pt-2">
          <TabsTrigger value="schema" className="flex-1">
            스키마
          </TabsTrigger>
          <TabsTrigger value="history" className="flex-1">
            질문 이력
          </TabsTrigger>
        </TabsList>
        <TabsContent value="schema" className="min-h-0 flex-1 overflow-y-auto p-3">
          <div className="flex flex-col gap-4">
            <div className="flex flex-col gap-1">
              <p className="px-1 text-[11px] font-semibold uppercase text-text-faint">노드</p>
              {nodes.map((node) => {
                const isOpen = openNode === node.label
                return (
                  <div key={node.label}>
                    <button
                      type="button"
                      onClick={() => setOpenNode(isOpen ? null : node.label)}
                      className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left hover:bg-panel-2"
                      aria-expanded={isOpen}
                      aria-label={`${node.label} 속성 ${isOpen ? '접기' : '펼치기'}`}
                    >
                      <NodeGlyphBadge nodeLabel={node.label} glyph={node.glyph} size={18} />
                      <span className="flex-1 text-[12.5px] font-semibold text-text">
                        {node.label}
                      </span>
                      <span
                        className={`text-text-faint transition-transform ${isOpen ? 'rotate-180' : ''}`}
                      >
                        ▾
                      </span>
                    </button>
                    {isOpen ? (
                      <div className="ml-7 flex flex-col gap-0.5 pb-1.5 text-[11px] text-text-faint">
                        <p>{node.description}</p>
                        <p className="font-mono">{node.properties.join(', ')}</p>
                      </div>
                    ) : null}
                  </div>
                )
              })}
            </div>
          </div>
          <div className="flex flex-col gap-1">
            <p className="px-1 text-[11px] font-semibold uppercase text-text-faint">관계 타입</p>
            {relationships.map((rel) => (
              <div key={rel.name} className="px-2 py-1">
                <p className="font-mono text-[11.5px] text-text">{rel.name}</p>
                <p className="text-[11px] text-text-faint">{rel.description}</p>
              </div>
            ))}
          </div>
        </TabsContent>
        <TabsContent value="history" className="min-h-0 flex-1 overflow-y-auto p-3">
          <div className="flex flex-col gap-1">
            {history.length === 0 ? (
              <p className="px-1 text-[12px] text-text-faint">아직 질문 이력이 없습니다.</p>
            ) : (
              history.map((item) => (
                <div
                  key={item.id}
                  className="group flex items-start gap-1 rounded-md px-2 py-1.5 hover:bg-panel-2"
                >
                  <button
                    type="button"
                    onClick={() => onSelectHistoryItem(item)}
                    className="flex flex-1 flex-col gap-0.5 text-left"
                  >
                    <p className="line-clamp-2 text-[12px] text-text">{item.query}</p>
                    <p className="text-[10px] text-text-faint">
                      {item.username} ·{' '}
                      {new Date(item.created_at).toLocaleString('ko-KR', {
                        year: 'numeric',
                        month: '2-digit',
                        day: '2-digit',
                        hour: '2-digit',
                        minute: '2-digit',
                      })}
                    </p>
                  </button>
                  <button
                    type="button"
                    onClick={(e) => {
                      e.stopPropagation()
                      onDeleteHistoryItem(item)
                    }}
                    aria-label="이 질문 이력 삭제"
                    className="mt-0.5 shrink-0 rounded p-0.5 text-text-faint opacity-0 hover:bg-panel hover:text-text group-hover:opacity-100"
                  >
                    <X size={13} />
                  </button>
                </div>
              ))
            )}
          </div>
        </TabsContent>
      </Tabs>
    </AppSidebar>
  )
}
