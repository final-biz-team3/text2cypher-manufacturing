import { useEffect, useLayoutEffect, useState } from 'react'
import { Loader2 } from 'lucide-react'
import { useLocation, useNavigate } from 'react-router-dom'
import { TopBar } from '@/components/layout/TopBar'
import { SchemaSidebar } from '@/components/layout/SchemaSidebar'
import { QueryInputBar } from '@/components/query/QueryInputBar'
import { NaturalLanguageAnswerBox } from '@/components/query/NaturalLanguageAnswerBox'
import { ClarificationPrompt } from '@/components/query/ClarificationPrompt'
import { GeneratedQueryPanel } from '@/components/result/GeneratedQueryPanel'
import { ResultEvidencePanel } from '@/components/result/ResultEvidencePanel'
import { useUiStore } from '@/store/useUiStore'
import { useAuthStore } from '@/store/useAuthStore'
import { useHealthStore } from '@/store/useHealthStore'
import { SCHEMA_NODES, RELATIONSHIPS } from '@/lib/schemaNodes'
import { sendChatQuery, ChatError, ClarificationNeededError } from '@/lib/chat'
import { fetchHistory } from '@/lib/history'
import { formatCypherError } from '@/lib/formatCypherError'
import { toDisplayResult } from '@/lib/displayResult'
import type { AmbiguousCandidate, HistoryEntry } from '@/lib/schemas'
import type { RetryAttempt, SelfCorrectionStep } from '@/types/query'

// 모호한 이름이 여러 개면 한 번에 하나씩 확정되므로, 지금까지 확정한 후보들과
// 원래 질문, 그리고 방금 받은 새 후보 목록을 함께 들고 있어야 한다
interface PendingClarification {
  query: string
  confirmedSoFar: AmbiguousCandidate['entity'][]
  message: string
  candidates: AmbiguousCandidate[]
}

const EXAMPLE_QUESTIONS: string[] = [
  '재고가 부족한 제품을 알려줘',
  '이 제품에 필요한 부품은 뭐야?',
  '부품 A의 공급업체를 알려줘',
  '폐기 수량이 많은 작업지시와 그 사유를 알려줘',
]

const READ_ONLY = true

// 재시도 이력을 "에러 없음/EMPTY_RESULT/그 외" 세 갈래로 나눠 타임라인 단계로 바꾼다.
// 실패 다음에 또 다른 시도가 이어졌다면(=실제로 재시도됨) "다시 시도합니다."를 덧붙인다.
function attemptsToSteps(prefix: string, attempts: RetryAttempt[]): SelfCorrectionStep[] {
  return attempts.map((attempt, index) => {
    const retried = attempt.error !== null && index < attempts.length - 1
    const detail =
      attempt.error === null
        ? '성공'
        : attempt.error === 'EMPTY_RESULT'
          ? '결과 없음'
          : formatCypherError(attempt.error)
    return {
      id: `${prefix}-${index}`,
      status: attempt.error === null ? 'success' : 'fail',
      title: `시도 ${index + 1}`,
      detail: retried ? `${detail} 다시 시도합니다.` : detail,
    }
  })
}

// 대시보드 화면 전체를 구성하는 최상위 컴포넌트.
// 질문 입력 → /chat 호출 → 결과 표시 → 이력 저장까지 대시보드의 핵심 흐름을 담당한다.
export function Dashboard() {
  const navigate = useNavigate()
  const location = useLocation()
  const user = useAuthStore((s) => s.user)
  const logout = useAuthStore((s) => s.logout)
  const neo4jConnected = useHealthStore((s) => s.neo4jConnected)
  const postgresConnected = useHealthStore((s) => s.postgresConnected)
  const [history, setHistory] = useState<HistoryEntry[]>([])
  // Chat에 새로 진입하면 이전 결과 대신 새 질문 화면을 보여준다. 대시보드에서
  // 전달한 질문 초안만 입력창의 초기값으로 사용한다.
  const [queryText, setQueryText] = useState(() => {
    const draftQuestion = (location.state as { draftQuestion?: unknown } | null)?.draftQuestion
    return typeof draftQuestion === 'string' ? draftQuestion : ''
  })
  // 질문 처리 중에는 여러 결과 컴포넌트가 같은 화면 상태를 공유한다. Chat에
  // 새로 진입할 때는 아래 useLayoutEffect에서 이 상태를 초기화한다.
  const activeScreen = useUiStore((s) => s.activeScreen)
  const setActiveScreen = useUiStore((s) => s.setActiveScreen)
  const result = useUiStore((s) => s.result)
  const setResult = useUiStore((s) => s.setResult)
  const errorMessage = useUiStore((s) => s.errorMessage)
  const setErrorMessage = useUiStore((s) => s.setErrorMessage)
  const queryPanelCollapsed = useUiStore((s) => s.queryPanelCollapsed)
  const toggleQueryPanelCollapsed = useUiStore((s) => s.toggleQueryPanelCollapsed)
  // 새로고침하면 사라져도 되는 휘발성 상태라 store(sessionStorage)가 아닌
  // 로컬 상태로 둔다 - useUiStore.ts의 clarify 리셋 참고.
  const [pendingClarification, setPendingClarification] = useState<PendingClarification | null>(
    null,
  )

  // sessionStorage에 이전 성공·오류 화면이 남아 있어도 첫 페인트 전에 질문 화면으로
  // 초기화한다. useEffect보다 먼저 실행해 예시 질문이 잠깐 보였다 사라지는 현상을 막는다.
  useLayoutEffect(() => {
    setActiveScreen('idle')
    setResult(null)
    setErrorMessage('')
  }, [setActiveScreen, setErrorMessage, setResult])

  // 대화기록을 다시 불러와 사이드바 목록을 갱신한다
  const refreshHistory = () => {
    fetchHistory()
      .then(setHistory)
      .catch((err: unknown) => console.error('fetchHistory failed:', err))
  }

  // 화면이 열릴 때 대화기록을 불러온다
  useEffect(() => {
    refreshHistory()
  }, [])

  useEffect(() => {
    const draftQuestion = (location.state as { draftQuestion?: unknown } | null)?.draftQuestion
    if (typeof draftQuestion === 'string' && draftQuestion.trim()) {
      navigate('/chat', { replace: true, state: null })
    }
  }, [location.state, navigate])

  // /chat을 호출하고 성공·모호함·에러 세 갈래로 화면 상태를 갱신하는 공통 로직.
  // confirmedSoFar는 직전 라운드까지 사용자가 확정한 후보들(모호한 이름이
  // 여러 개면 한 번에 하나씩 확정되므로 누적해서 다시 보낸다).
  const runChatQuery = async (question: string, confirmedSoFar: AmbiguousCandidate['entity'][]) => {
    setActiveScreen('loading')
    try {
      const confirmedEntity =
        confirmedSoFar.length === 0
          ? undefined
          : confirmedSoFar.length === 1
            ? confirmedSoFar[0]
            : confirmedSoFar
      const response = await sendChatQuery(question, confirmedEntity)
      setPendingClarification(null)
      setResult(toDisplayResult(response))
      setActiveScreen('success')
      refreshHistory()
    } catch (err) {
      if (err instanceof ClarificationNeededError) {
        setPendingClarification({
          query: question,
          confirmedSoFar,
          message: err.message,
          candidates: err.candidates,
        })
        setActiveScreen('clarify')
        return
      }
      setPendingClarification(null)
      setErrorMessage(err instanceof ChatError ? err.message : '질의 처리 중 오류가 발생했습니다')
      setActiveScreen('error')
    }
  }

  // 질문 제출: /chat을 호출하고 결과·이력을 갱신한다
  const handleSubmit = async () => {
    const question = queryText.trim()
    if (!question) return
    await runChatQuery(question, [])
  }

  // 모호한 이름 후보 중 하나를 선택하면 확정 목록에 더해 같은 질문을 재요청한다.
  // 입력창에는 방금 고른 후보 이름을 반영해 선택이 실제로 적용됐음을 보여준다.
  const handleSelectCandidate = async (candidate: AmbiguousCandidate) => {
    if (!pendingClarification) return
    setQueryText(candidate.name)
    await runChatQuery(pendingClarification.query, [
      ...pendingClarification.confirmedSoFar,
      candidate.entity,
    ])
  }

  const handleCancelClarification = () => {
    setPendingClarification(null)
    setActiveScreen('idle')
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
    setPendingClarification(null)
    if (queryPanelCollapsed) toggleQueryPanelCollapsed()
  }

  const queryInputBar = (
    <QueryInputBar value={queryText} onChange={setQueryText} onSubmit={handleSubmit} />
  )

  return (
    <div className="flex h-screen flex-col bg-bg">
      <TopBar
        connected={neo4jConnected}
        postgresConnected={postgresConnected}
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
          onNavigateDashboard={() => navigate('/dashboard')}
          onNavigateChat={handleNavigateHome}
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
          {activeScreen === 'clarify' && pendingClarification && (
            <div className="flex flex-col gap-4">
              {queryInputBar}
              <ClarificationPrompt
                message={pendingClarification.message}
                candidates={pendingClarification.candidates}
                onSelect={handleSelectCandidate}
                onCancel={handleCancelClarification}
              />
            </div>
          )}
          {activeScreen === 'success' && result && (
            <div className="flex flex-col gap-4">
              {queryInputBar}
              <NaturalLanguageAnswerBox answer={result.answer} />
              <ResultEvidencePanel key={result.query} {...result} />
            </div>
          )}
        </main>
        {activeScreen === 'success' &&
        result &&
        (result.sql ||
          result.cypher ||
          result.sqlAttempts.length > 0 ||
          result.cypherAttempts.length > 0) ? (
          <GeneratedQueryPanel
            queries={[
              ...(result.sql
                ? [{ label: '생성된 SQL', language: 'sql' as const, query: result.sql }]
                : []),
              ...(result.cypher
                ? [{ label: '생성된 Cypher', language: 'cypher' as const, query: result.cypher }]
                : []),
            ]}
            sqlAttempts={attemptsToSteps('sql', result.sqlAttempts)}
            cypherAttempts={attemptsToSteps('cypher', result.cypherAttempts)}
            collapsed={queryPanelCollapsed}
            onToggleCollapsed={toggleQueryPanelCollapsed}
          />
        ) : null}
      </div>
    </div>
  )
}
