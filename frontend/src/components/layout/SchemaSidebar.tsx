import { useState } from 'react'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { useUiStore } from '@/store/useUiStore'
import type { SchemaNode, SchemaRelationship } from '@/types/query'

const SCHEMA_NODES: SchemaNode[] = [
  { label: 'Lot', glyph: 'L', description: '생산 배치 단위', properties: ['lot_id', 'product_code', 'created_at'] },
  { label: 'Process', glyph: 'P', description: '공정 단계', properties: ['process_name', 'sequence'] },
  { label: 'Equipment', glyph: 'EQ', description: '설비', properties: ['equipment_id', 'line'] },
  { label: 'Material', glyph: 'M', description: '투입 자재', properties: ['material_code', 'lot_no'] },
  { label: 'Defect', glyph: 'D', description: '불량 기록', properties: ['defect_code', 'severity', 'detected_at'] },
]

const RELATIONSHIPS: SchemaRelationship[] = [
  { name: 'FOLLOWS', description: '공정 순서' },
  { name: 'PROCESSED_AT', description: '설비 투입' },
  { name: 'HAS_DEFECT', description: '불량 발생' },
  { name: 'CONSUMES', description: '자재 소모' },
]

const NODE_COLOR_CLASS: Record<SchemaNode['label'], string> = {
  Lot: 'bg-node-lot',
  Process: 'bg-node-process',
  Equipment: 'bg-node-equipment',
  Material: 'bg-node-material',
  Defect: 'bg-node-defect',
}

export function SchemaSidebar() {
  const historyTab = useUiStore((s) => s.historyTab)
  const setHistoryTab = useUiStore((s) => s.setHistoryTab)
  const [openNode, setOpenNode] = useState<string | null>(null)

  return (
    <aside className="flex w-[240px] shrink-0 flex-col overflow-y-auto border-r border-border bg-panel">
      <Tabs value={historyTab} onValueChange={(v) => setHistoryTab(v as 'schema' | 'history')}>
        <TabsList variant="line" className="w-full px-2 pt-2">
          <TabsTrigger value="schema" className="flex-1">
            스키마
          </TabsTrigger>
          <TabsTrigger value="history" className="flex-1">
            질문 이력
          </TabsTrigger>
        </TabsList>
        <TabsContent value="schema" className="flex flex-col gap-4 p-3">
          <div className="flex flex-col gap-1">
            <p className="px-1 text-[11px] font-semibold uppercase text-text-faint">노드</p>
            {SCHEMA_NODES.map((node) => {
              const isOpen = openNode === node.label
              return (
                <div key={node.label}>
                  <button
                    type="button"
                    onClick={() => setOpenNode(isOpen ? null : node.label)}
                    className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left hover:bg-panel-2"
                  >
                    <span
                      className={`flex size-[18px] shrink-0 items-center justify-center rounded-full text-[9px] font-bold text-white ${NODE_COLOR_CLASS[node.label]}`}
                    >
                      {node.glyph}
                    </span>
                    <span className="flex-1 text-[12.5px] font-semibold text-text">{node.label}</span>
                    <span className={`text-text-faint transition-transform ${isOpen ? 'rotate-180' : ''}`}>▾</span>
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
          <div className="flex flex-col gap-1">
            <p className="px-1 text-[11px] font-semibold uppercase text-text-faint">관계 타입</p>
            {RELATIONSHIPS.map((rel) => (
              <div key={rel.name} className="px-2 py-1">
                <p className="font-mono text-[11.5px] text-text">{rel.name}</p>
                <p className="text-[11px] text-text-faint">{rel.description}</p>
              </div>
            ))}
          </div>
        </TabsContent>
        <TabsContent value="history" className="p-3">
          <p className="px-1 text-[12px] text-text-faint">아직 질문 이력이 없습니다.</p>
        </TabsContent>
      </Tabs>
    </aside>
  )
}
