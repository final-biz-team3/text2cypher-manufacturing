import { useState } from 'react'
import { TopBar } from '@/components/layout/TopBar'
import { SchemaSidebar } from '@/components/layout/SchemaSidebar'
import { QueryInputBar } from '@/components/query/QueryInputBar'
import { ExampleQuestionCard } from '@/components/query/ExampleQuestionCard'
import { NaturalLanguageAnswerBox } from '@/components/query/NaturalLanguageAnswerBox'
import { ResultsTable } from '@/components/result/ResultsTable'
import { CypherSlidePanel } from '@/components/result/CypherSlidePanel'
import { useUiStore } from '@/store/useUiStore'
import { useAuthStore } from '@/store/useAuthStore'
import { SCHEMA_NODES, RELATIONSHIPS } from '@/lib/schemaNodes'
import { sendChatQuery, ChatError } from '@/lib/chat'
import type { ChatResponse } from '@/lib/schemas'
import type { HistoryItem, NodeLabel, ResultColumn } from '@/types/query'

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

interface DisplayResult {
  answer: string
  cypher: string | null
  columns: ResultColumn[]
  rows: Record<string, string>[]
}

// /chat 응답을 화면에 뿌릴 수 있는 형태로 정리한다
function toDisplayResult(response: ChatResponse): DisplayResult {
  const rowsRaw = response.sql_result?.result ?? response.graph_result?.result ?? []
  const columns: ResultColumn[] =
    rowsRaw.length > 0 ? Object.keys(rowsRaw[0]).map((key) => ({ key, label: key })) : []
  const rows = rowsRaw.map((row) =>
    Object.fromEntries(
      Object.entries(row).map(([key, value]) => [key, value == null ? '' : String(value)]),
    ),
  )
  return {
    answer: response.final_answer ?? '답변을 생성하지 못했습니다.',
    cypher: response.cypher_query ?? null,
    columns,
    rows,
  }
}

// 대시보드 화면 전체를 구성하는 최상위 컴포넌트.
// 질문 입력 → /chat 호출 → 결과 표시 → 이력 저장까지 대시보드의 핵심 흐름을 담당한다.
export function Dashboard() {
  const user = useAuthStore((s) => s.user)
  const logout = useAuthStore((s) => s.logout)
  const [queryText, setQueryText] = useState('')
  const [history, setHistory] = useState<HistoryItem[]>([])
  const [result, setResult] = useState<DisplayResult | null>(null)
  const [errorMessage, setErrorMessage] = useState('')
  // 화면 단계(activeScreen)와 패널 열림/접힘 상태는 여러 컴포넌트가 공유해야 해서 전역 store에 둔다.
  const activeScreen = useUiStore((s) => s.activeScreen)
  const setActiveScreen = useUiStore((s) => s.setActiveScreen)
  const cypherCollapsed = useUiStore((s) => s.cypherCollapsed)
  const toggleCypherCollapsed = useUiStore((s) => s.toggleCypherCollapsed)

  // 질문 제출: /chat을 호출하고 결과를 이력에 기록한다
  const handleSubmit = async () => {
    const question = queryText.trim()
    if (!question) return
    setActiveScreen('loading')
    try {
      const response = await sendChatQuery(question)
      setResult(toDisplayResult(response))
      setHistory((prev) => [
        { id: generateHistoryId(), question, submittedAt: Date.now() },
        ...prev,
      ])
      setActiveScreen('success')
    } catch (err) {
      setErrorMessage(err instanceof ChatError ? err.message : '질의 처리 중 오류가 발생했습니다')
      setActiveScreen('error')
    }
  }

  // 홈으로 돌아갈 때는 이전 질문의 잔여 UI 상태(Cypher 패널)도 함께 초기화해서
  // 다음 질문 결과에 이전 상태가 그대로 남지 않도록 한다.
  const handleNavigateHome = () => {
    setActiveScreen('idle')
    setQueryText('')
    setResult(null)
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
          {activeScreen === 'loading' && (
            <div className="flex flex-1 flex-col items-center justify-center gap-4">
              <p className="text-sm text-text-muted">답변을 생성하는 중입니다…</p>
            </div>
          )}
          {activeScreen === 'error' && (
            <div className="flex flex-1 flex-col items-center justify-center gap-4">
              <p className="text-sm text-fail">{errorMessage}</p>
              <div className="w-full max-w-2xl">{queryInputBar}</div>
            </div>
          )}
          {activeScreen === 'success' && result && (
            <div className="flex flex-col gap-4">
              {queryInputBar}
              <NaturalLanguageAnswerBox answer={result.answer} />
              {result.columns.length > 0 ? (
                <ResultsTable columns={result.columns} rows={result.rows} />
              ) : null}
            </div>
          )}
        </main>
        {activeScreen === 'success' && result?.cypher ? (
          <CypherSlidePanel
            cypher={result.cypher}
            collapsed={cypherCollapsed}
            onToggleCollapsed={toggleCypherCollapsed}
          />
        ) : null}
      </div>
    </div>
  )
}
