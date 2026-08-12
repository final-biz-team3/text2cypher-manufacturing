import { useState } from 'react'
import { TopBar } from '@/components/layout/TopBar'
import { SchemaSidebar } from '@/components/layout/SchemaSidebar'
import { QueryInputBar } from '@/components/query/QueryInputBar'
import { ExampleQuestionCard } from '@/components/query/ExampleQuestionCard'
import { NaturalLanguageAnswerBox } from '@/components/query/NaturalLanguageAnswerBox'
import { FollowUpChips } from '@/components/query/FollowUpChips'
import { PathGraphCanvas } from '@/components/graph/PathGraphCanvas'
import { ResultsTable } from '@/components/result/ResultsTable'
import { EvidencePanel } from '@/components/result/EvidencePanel'
import { SelfCorrectionTimeline } from '@/components/result/SelfCorrectionTimeline'
import { CypherCard } from '@/components/result/CypherCard'
import { useUiStore } from '@/store/useUiStore'
import type { QueryResult } from '@/types/query'

const MOCK_RESULT: QueryResult = {
  answer:
    'LOT-2041은 세정을 거쳐 식각 공정에서 설비 EQ-07에 투입되었고, 해당 지점에서 불량 D-114가 기록되었습니다.',
  cypher: `MATCH (l:Lot {lot_id:'LOT-2041'})-[:PROCESSED_AT]->(p1:Process)-[:FOLLOWS]->(p2:Process)
MATCH (p2)-[:PROCESSED_AT]->(eq:Equipment)
OPTIONAL MATCH (p2)-[:HAS_DEFECT]->(d:Defect)
RETURN l, p1, p2, eq, d`,
  columns: [
    { key: 'lot', label: 'Lot' },
    { key: 'process', label: 'Process' },
    { key: 'equipment', label: 'Equipment' },
    { key: 'defect', label: 'Defect' },
  ],
  rows: [
    { lot: 'LOT-2041', process: '세정', equipment: '—', defect: '—' },
    { lot: 'LOT-2041', process: '식각', equipment: 'EQ-07', defect: 'D-114 (Major)' },
  ],
  timeline: [
    { id: '1', status: 'success', title: 'Cypher 생성 (시도 1)', detail: '스키마 기반 쿼리 생성 완료', elapsedMs: 700 },
    {
      id: '2',
      status: 'fail',
      title: '실행 (시도 1) — 실패',
      detail: '관계 오류: CAUSED_BY는 존재하지 않는 관계 타입',
      elapsedMs: 500,
    },
    {
      id: '3',
      status: 'warn',
      title: '스키마 재주입 후 재생성 (시도 2)',
      detail: '관계 타입 목록을 컨텍스트에 포함',
      elapsedMs: 600,
    },
    { id: '4', status: 'success', title: '실행 (시도 2) — 성공', detail: '1.4초 · 3행 반환', elapsedMs: 1400 },
  ],
}

const EXAMPLE_QUESTIONS: {
  kind: '경로추적' | '집계'
  question: string
  path: { glyph: string; label: string }[]
}[] = [
  {
    kind: '경로추적',
    question: 'LOT-2041에서 발생한 불량의 원인 경로를 찾아줘',
    path: [
      { glyph: 'L', label: 'LOT-2041' },
      { glyph: 'P', label: '식각' },
      { glyph: 'D', label: 'D-114' },
    ],
  },
  {
    kind: '집계',
    question: '지난 분기 작업장별 폐기 수량과 주요 폐기 사유를 알려줘',
    path: [
      { glyph: 'EQ', label: '작업장' },
      { glyph: 'D', label: '폐기 사유' },
    ],
  },
]

const FOLLOW_UP_QUESTIONS = ['이 답의 근거를 더 자세히', 'EQ-07의 최근 불량 이력은?', '같은 유형의 다른 Lot도 있어?']

export function Dashboard() {
  const [queryText, setQueryText] = useState('')
  const activeScreen = useUiStore((s) => s.activeScreen)
  const setActiveScreen = useUiStore((s) => s.setActiveScreen)
  const evidencePanelOpen = useUiStore((s) => s.evidencePanelOpen)
  const toggleEvidencePanel = useUiStore((s) => s.toggleEvidencePanel)
  const cypherCollapsed = useUiStore((s) => s.cypherCollapsed)
  const toggleCypherCollapsed = useUiStore((s) => s.toggleCypherCollapsed)

  const handleSubmit = () => {
    if (!queryText.trim()) return
    setActiveScreen('success')
  }

  return (
    <div className="flex h-screen flex-col bg-bg">
      <TopBar />
      <div className="flex flex-1 overflow-hidden">
        <SchemaSidebar />
        <main className="flex flex-1 flex-col gap-4 overflow-y-auto p-6">
          <QueryInputBar value={queryText} onChange={setQueryText} onSubmit={handleSubmit} />
          {activeScreen === 'idle' ? (
            <div className="grid grid-cols-3 gap-3">
              {EXAMPLE_QUESTIONS.map((example) => (
                <ExampleQuestionCard
                  key={example.question}
                  kind={example.kind}
                  question={example.question}
                  path={example.path}
                  onClick={() => setQueryText(example.question)}
                />
              ))}
            </div>
          ) : (
            <div className="flex flex-col gap-4">
              <NaturalLanguageAnswerBox answer={MOCK_RESULT.answer} />
              <PathGraphCanvas />
              <ResultsTable columns={MOCK_RESULT.columns} rows={MOCK_RESULT.rows} />
              <EvidencePanel open={evidencePanelOpen} onToggle={toggleEvidencePanel}>
                <SelfCorrectionTimeline steps={MOCK_RESULT.timeline} />
                <CypherCard
                  cypher={MOCK_RESULT.cypher}
                  collapsed={cypherCollapsed}
                  onToggleCollapsed={toggleCypherCollapsed}
                />
              </EvidencePanel>
              <FollowUpChips questions={FOLLOW_UP_QUESTIONS} onSelect={setQueryText} />
            </div>
          )}
        </main>
      </div>
    </div>
  )
}
