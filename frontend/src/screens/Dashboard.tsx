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
import { CypherSlidePanel } from '@/components/result/CypherSlidePanel'
import { useUiStore } from '@/store/useUiStore'
import { useAuthStore } from '@/store/useAuthStore'
import { SCHEMA_NODES, RELATIONSHIPS } from '@/lib/schemaNodes'
import type { HistoryItem, NodeLabel, QueryResult } from '@/types/query'

// TODO: 아래 FOLLOW_UP_QUESTIONS는 백엔드 연동 전까지 사용하는 목(mock) 데이터다.
// 실제 API 연동 시 이 상수들과 handleSubmit의 목 처리 로직을 서버 응답으로 대체해야 한다.

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
    {
      id: '1',
      status: 'success',
      title: 'Cypher 생성 (시도 1)',
      detail: '스키마 기반 쿼리 생성 완료',
      elapsedMs: 700,
    },
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
    {
      id: '4',
      status: 'success',
      title: '실행 (시도 2) — 성공',
      detail: '1.4초 · 3행 반환',
      elapsedMs: 1400,
    },
  ],
}

const EXAMPLE_QUESTIONS: {
  kind: '경로추적' | '집계'
  question: string
  path: { glyph: string; label: string; nodeLabel: NodeLabel }[]
}[] = [
  {
    kind: '경로추적',
    question: 'LOT-2041에서 발생한 불량의 원인 경로를 찾아줘',
    path: [
      { glyph: 'L', label: 'LOT-2041', nodeLabel: 'Lot' },
      { glyph: 'P', label: '식각', nodeLabel: 'Process' },
      { glyph: 'D', label: 'D-114', nodeLabel: 'Defect' },
    ],
  },
  {
    kind: '집계',
    question: '지난 분기 작업장별 폐기 수량과 주요 폐기 사유를 알려줘',
    path: [
      { glyph: 'EQ', label: '작업장', nodeLabel: 'Equipment' },
      { glyph: 'D', label: '폐기 사유', nodeLabel: 'Defect' },
    ],
  },
]

const FOLLOW_UP_QUESTIONS = [
  '이 답의 근거를 더 자세히',
  'EQ-07의 최근 불량 이력은?',
  '같은 유형의 다른 Lot도 있어?',
]

const CONNECTED = false
const CONNECTION_ENDPOINT = 'bolt://prod-kg-01'
const READ_ONLY = true

function generateHistoryId(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID()
  }
  // crypto.randomUUID는 보안 컨텍스트(HTTPS/localhost)에서만 존재한다.
  return `${Date.now()}-${Math.random().toString(36).slice(2)}`
}

// 대시보드 화면 전체를 구성하는 최상위 컴포넌트.
// 질문 입력 → 목 결과 표시 → 이력 저장까지 대시보드의 핵심 흐름을 담당한다.
export function Dashboard() {
  const user = useAuthStore((s) => s.user)
  const logout = useAuthStore((s) => s.logout)
  const [queryText, setQueryText] = useState('')
  const [history, setHistory] = useState<HistoryItem[]>([])
  // 화면 단계(activeScreen)와 패널 열림/접힘 상태는 여러 컴포넌트가 공유해야 해서 전역 store에 둔다.
  const activeScreen = useUiStore((s) => s.activeScreen)
  const setActiveScreen = useUiStore((s) => s.setActiveScreen)
  const evidencePanelOpen = useUiStore((s) => s.evidencePanelOpen)
  const toggleEvidencePanel = useUiStore((s) => s.toggleEvidencePanel)
  const cypherCollapsed = useUiStore((s) => s.cypherCollapsed)
  const toggleCypherCollapsed = useUiStore((s) => s.toggleCypherCollapsed)

  // 질문 제출: 이력에 기록하고 성공 화면으로 전환한다(현재는 항상 MOCK_RESULT를 보여준다)
  const handleSubmit = () => {
    const question = queryText.trim()
    if (!question) return
    setHistory((prev) => [{ id: generateHistoryId(), question, submittedAt: Date.now() }, ...prev])
    setActiveScreen('success')
  }

  // 홈으로 돌아갈 때는 이전 질문의 잔여 UI 상태(근거 패널, Cypher 패널)도 함께 초기화해서
  // 다음 질문 결과에 이전 상태가 그대로 남지 않도록 한다.
  const handleNavigateHome = () => {
    setActiveScreen('idle')
    setQueryText('')
    if (evidencePanelOpen) toggleEvidencePanel()
    if (cypherCollapsed) toggleCypherCollapsed()
  }

  const queryInputBar = (
    <QueryInputBar value={queryText} onChange={setQueryText} onSubmit={handleSubmit} />
  )

  return (
    <div className="flex h-screen flex-col bg-bg">
      <TopBar
        connected={CONNECTED}
        connectionEndpoint={CONNECTION_ENDPOINT}
        readOnly={READ_ONLY}
        onNavigateHome={handleNavigateHome}
        username={user?.username}
        onLogout={logout}
      />
      <div className="flex flex-1 overflow-hidden">
        <SchemaSidebar
          nodes={SCHEMA_NODES}
          relationships={RELATIONSHIPS}
          history={history}
          onSelectHistoryItem={setQueryText}
        />
        <main className="flex flex-1 flex-col overflow-y-auto p-6">
          {activeScreen === 'idle' && (
            <div className="flex flex-1 flex-col items-center justify-center gap-6">
              <div className="flex flex-col items-center gap-1 text-center">
                <h1 className="text-lg font-semibold text-text">
                  공정 데이터에 대해 무엇이든 물어보세요
                </h1>
                <p className="text-[13px] text-text-muted">
                  Neo4j 지식그래프 기반으로 공정·품질 데이터를 자연어로 질의할 수 있습니다
                </p>
              </div>
              <div className="w-full max-w-2xl">{queryInputBar}</div>
              {history.length === 0 ? (
                <div className="grid w-full max-w-2xl grid-cols-2 gap-3">
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
              ) : null}
            </div>
          )}
          {activeScreen === 'success' && (
            <div className="flex flex-col gap-4">
              {queryInputBar}
              <NaturalLanguageAnswerBox answer={MOCK_RESULT.answer} />
              <PathGraphCanvas />
              <ResultsTable columns={MOCK_RESULT.columns} rows={MOCK_RESULT.rows} />
              <EvidencePanel open={evidencePanelOpen} onToggle={toggleEvidencePanel}>
                <SelfCorrectionTimeline steps={MOCK_RESULT.timeline} />
              </EvidencePanel>
              <FollowUpChips questions={FOLLOW_UP_QUESTIONS} onSelect={setQueryText} />
            </div>
          )}
        </main>
        {activeScreen === 'success' ? (
          <CypherSlidePanel
            cypher={MOCK_RESULT.cypher}
            collapsed={cypherCollapsed}
            onToggleCollapsed={toggleCypherCollapsed}
          />
        ) : null}
      </div>
    </div>
  )
}
