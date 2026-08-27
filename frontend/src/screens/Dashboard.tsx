import { useEffect, useState } from 'react'
import { Loader2 } from 'lucide-react'
import { TopBar } from '@/components/layout/TopBar'
import { SchemaSidebar } from '@/components/layout/SchemaSidebar'
import { QueryInputBar } from '@/components/query/QueryInputBar'
import { NaturalLanguageAnswerBox } from '@/components/query/NaturalLanguageAnswerBox'
import { ResultsTable } from '@/components/result/ResultsTable'
import { GeneratedQueryPanel } from '@/components/result/GeneratedQueryPanel'
import { EvidencePanel } from '@/components/result/EvidencePanel'
import { SelfCorrectionTimeline } from '@/components/result/SelfCorrectionTimeline'
import { useUiStore } from '@/store/useUiStore'
import { useAuthStore } from '@/store/useAuthStore'
import { useHealthStore } from '@/store/useHealthStore'
import { SCHEMA_NODES, RELATIONSHIPS } from '@/lib/schemaNodes'
import { sendChatQuery, ChatError } from '@/lib/chat'
import { fetchHistory } from '@/lib/history'
import type { ChatResponse, HistoryEntry } from '@/lib/schemas'
import type { ResultColumn, SelfCorrectionStep } from '@/types/query'

const EXAMPLE_QUESTIONS: string[] = [
  '재고가 부족한 제품을 알려줘',
  '이 제품에 필요한 부품은 뭐야?',
  '부품 A의 공급업체를 알려줘',
  '폐기 수량이 많은 작업지시와 그 사유를 알려줘',
]

const READ_ONLY = true

interface RetryAttempt {
  query: string
  error: string | null
}

interface DisplayResult {
  answer: string
  sql: string | null
  cypher: string | null
  columns: ResultColumn[]
  rows: Record<string, string>[]
  sqlAttempts: RetryAttempt[]
  cypherAttempts: RetryAttempt[]
}

// /chat 응답이나 대화기록 항목을 화면에 뿌릴 수 있는 형태로 정리한다
function toDisplayResult(response: ChatResponse | HistoryEntry): DisplayResult {
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
    sql: response.sql_query ?? null,
    cypher: response.cypher_query ?? null,
    columns,
    rows,
    sqlAttempts: response.sql_result?.attempts ?? [],
    cypherAttempts: response.graph_result?.attempts ?? [],
  }
}

// 재시도 이력을 "에러 없음/EMPTY_RESULT/그 외" 세 갈래로 나눠 타임라인 단계로 바꾼다
function attemptsToSteps(prefix: string, attempts: RetryAttempt[]): SelfCorrectionStep[] {
  return attempts.map((attempt, index) => ({
    id: `${prefix}-${index}`,
    status: attempt.error === null ? 'success' : 'fail',
    title: `시도 ${index + 1}`,
    detail:
      attempt.error === null
        ? '성공'
        : attempt.error === 'EMPTY_RESULT'
          ? '결과 없음'
          : attempt.error,
  }))
}

// 대시보드 화면 전체를 구성하는 최상위 컴포넌트.
// 질문 입력 → /chat 호출 → 결과 표시 → 이력 저장까지 대시보드의 핵심 흐름을 담당한다.
export function Dashboard() {
  const user = useAuthStore((s) => s.user)
  const logout = useAuthStore((s) => s.logout)
  const neo4jConnected = useHealthStore((s) => s.neo4jConnected)
  const [queryText, setQueryText] = useState('')
  const [history, setHistory] = useState<HistoryEntry[]>([])
  const [result, setResult] = useState<DisplayResult | null>(null)
  const [errorMessage, setErrorMessage] = useState('')
  // 화면 단계(activeScreen)와 패널 열림/접힘 상태는 여러 컴포넌트가 공유해야 해서 전역 store에 둔다.
  const activeScreen = useUiStore((s) => s.activeScreen)
  const setActiveScreen = useUiStore((s) => s.setActiveScreen)
  const queryPanelCollapsed = useUiStore((s) => s.queryPanelCollapsed)
  const toggleQueryPanelCollapsed = useUiStore((s) => s.toggleQueryPanelCollapsed)
  const evidencePanelOpen = useUiStore((s) => s.evidencePanelOpen)
  const toggleEvidencePanel = useUiStore((s) => s.toggleEvidencePanel)

  // 화면이 열릴 때 대화기록을 불러온다
  useEffect(() => {
    fetchHistory()
      .then(setHistory)
      .catch((err: unknown) => console.error('fetchHistory failed:', err))
  }, [])

  // 질문 제출: /chat을 호출하고 결과·이력을 갱신한다
  const handleSubmit = async () => {
    const question = queryText.trim()
    if (!question) return
    setActiveScreen('loading')
    try {
      const response = await sendChatQuery(question)
      setResult(toDisplayResult(response))
      setActiveScreen('success')
      fetchHistory()
        .then(setHistory)
        .catch((err: unknown) => console.error('fetchHistory failed:', err))
    } catch (err) {
      setErrorMessage(err instanceof ChatError ? err.message : '질의 처리 중 오류가 발생했습니다')
      setActiveScreen('error')
    }
  }

  // 대화기록 목록에서 항목을 클릭하면 재호출 없이 저장된 내용을 그대로 다시 보여준다
  const handleSelectHistoryItem = (item: HistoryEntry) => {
    setQueryText(item.query)
    setResult(toDisplayResult(item))
    setActiveScreen('success')
  }

  // 홈으로 돌아갈 때는 이전 질문의 잔여 UI 상태(쿼리 패널)도 함께 초기화해서
  // 다음 질문 결과에 이전 상태가 그대로 남지 않도록 한다.
  const handleNavigateHome = () => {
    setActiveScreen('idle')
    setQueryText('')
    setResult(null)
    if (queryPanelCollapsed) toggleQueryPanelCollapsed()
  }

  const queryInputBar = (
    <QueryInputBar value={queryText} onChange={setQueryText} onSubmit={handleSubmit} />
  )

  return (
    <div className="flex h-screen flex-col bg-bg">
      <TopBar
        connected={neo4jConnected}
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
          onSelectHistoryItem={handleSelectHistoryItem}
        />
        <main className="flex flex-1 flex-col overflow-y-auto p-6">
          {activeScreen === 'idle' && (
            <div className="flex flex-1 flex-col items-center justify-center gap-6">
              <div className="flex flex-col items-center gap-1 text-center">
                <h1 className="text-lg font-semibold text-text">
                  제조 데이터, 궁금한 것을 질문하세요.
                </h1>
                <p className="text-[13px] text-text-muted">
                  제품, 재고, 부품, 공급업체 등 필요한 정보를 질문하면 관련 데이터를 찾아 답변해
                  드립니다.
                </p>
              </div>
              <div className="w-full max-w-2xl">{queryInputBar}</div>
              {history.length === 0 ? (
                <div className="w-full max-w-2xl">
                  <p className="mb-2 text-[12px] font-semibold text-text-faint">
                    이렇게 질문해 보세요
                  </p>
                  <ul className="flex flex-col gap-1.5">
                    {EXAMPLE_QUESTIONS.map((question) => (
                      <li key={question}>
                        <button
                          type="button"
                          onClick={() => setQueryText(question)}
                          className="w-full rounded-md border border-border bg-panel px-3 py-2 text-left text-[12.5px] text-text transition-colors hover:border-border-strong"
                        >
                          {question}
                        </button>
                      </li>
                    ))}
                  </ul>
                </div>
              ) : null}
            </div>
          )}
          {activeScreen === 'loading' && (
            <div className="flex flex-1 flex-col items-center justify-center gap-4">
              <Loader2 className="size-6 animate-spin text-text-muted" />
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
              {result.sqlAttempts.length > 0 || result.cypherAttempts.length > 0 ? (
                <EvidencePanel open={evidencePanelOpen} onToggle={toggleEvidencePanel}>
                  {result.sqlAttempts.length > 0 ? (
                    <div>
                      <p className="mb-1.5 text-[11px] font-semibold uppercase text-text-faint">
                        SQL 시도
                      </p>
                      <SelfCorrectionTimeline steps={attemptsToSteps('sql', result.sqlAttempts)} />
                    </div>
                  ) : null}
                  {result.cypherAttempts.length > 0 ? (
                    <div>
                      <p className="mb-1.5 text-[11px] font-semibold uppercase text-text-faint">
                        Cypher 시도
                      </p>
                      <SelfCorrectionTimeline
                        steps={attemptsToSteps('cypher', result.cypherAttempts)}
                      />
                    </div>
                  ) : null}
                </EvidencePanel>
              ) : null}
            </div>
          )}
        </main>
        {activeScreen === 'success' && result && (result.sql || result.cypher) ? (
          <GeneratedQueryPanel
            queries={[
              ...(result.sql ? [{ label: '생성된 SQL', query: result.sql }] : []),
              ...(result.cypher ? [{ label: '생성된 Cypher', query: result.cypher }] : []),
            ]}
            collapsed={queryPanelCollapsed}
            onToggleCollapsed={toggleQueryPanelCollapsed}
          />
        ) : null}
      </div>
    </div>
  )
}
