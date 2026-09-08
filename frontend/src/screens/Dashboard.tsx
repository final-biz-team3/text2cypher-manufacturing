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
import type { ConversationTurn } from '@/store/useUiStore'
import { useAuthStore } from '@/store/useAuthStore'
import { useHealthStore } from '@/store/useHealthStore'
import { SCHEMA_NODES, RELATIONSHIPS } from '@/lib/schemaNodes'
import { sendChatQuery, ChatError, ClarificationNeededError } from '@/lib/chat'
import { deleteHistory, fetchHistory } from '@/lib/history'
import { formatCypherError } from '@/lib/formatCypherError'
import { toDisplayResult } from '@/lib/displayResult'
import type { AmbiguousCandidate, ConfirmedEntity, HistoryEntry } from '@/lib/schemas'
import type { RetryAttempt, SelfCorrectionStep } from '@/types/query'

// 모호한 이름이 여러 개면 한 번에 하나씩 확정되므로, 지금까지 확정한 후보들과
// 원래 질문, 그리고 방금 받은 새 후보 목록을 함께 들고 있어야 한다.
// 어느 대화 턴에 대한 확인인지 식별해야 후보 선택 시 같은 턴을 갱신할 수 있다.
interface PendingClarification {
  turnId: string
  query: string
  confirmedSoFar: ConfirmedEntity[]
  message: string
  candidates: AmbiguousCandidate[]
  // 지금 candidates가 나온 원본 추출 이름 - 사용자가 후보를 고르면 이 값을
  // ConfirmedEntity.forName에 실어 보내, 서버가 "이번 재확인"과 "이름이
  // 우연히 비슷한 별개의 새 대상"을 구분할 수 있게 한다.
  lookupName: string
}

const EXAMPLE_QUESTIONS: string[] = [
  '외부에서 구매하는 부품 알려줘',
  '제품 Paint - Black의 안전재고, 실제 재고와 부족 수량을 알려줘.',
  '반려 수량이 많은 공급업체 상위 5곳을 알려줘.',
  '작업오더 17747이 방문한 작업장과 라우팅 공정을 실제 진행 순서대로 나열해줘',
  'HL Road Frame - Black, 58의 말단 BOM 자재 중 보유 재고가 안전 수준보다 낮은 것과 부족분을 계산해줘',
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
  // 대화 턴(질문 1개 + 처리 상태/결과) 목록. Chat에 새로 진입할 때는 아래
  // useLayoutEffect에서 이 목록을 초기화한다.
  const turns = useUiStore((s) => s.turns)
  const addTurn = useUiStore((s) => s.addTurn)
  const updateTurn = useUiStore((s) => s.updateTurn)
  const removeTurn = useUiStore((s) => s.removeTurn)
  const clearTurns = useUiStore((s) => s.clearTurns)
  const queryPanelCollapsed = useUiStore((s) => s.queryPanelCollapsed)
  const toggleQueryPanelCollapsed = useUiStore((s) => s.toggleQueryPanelCollapsed)
  // 새로고침하면 사라져도 되는 휘발성 상태라 store(sessionStorage)가 아닌
  // 로컬 상태로 둔다 - useUiStore.ts의 clarify 리셋 참고.
  const [pendingClarification, setPendingClarification] = useState<PendingClarification | null>(
    null,
  )

  // sessionStorage에 이전 대화가 남아 있어도 첫 페인트 전에 새 질문 화면으로
  // 초기화한다. useEffect보다 먼저 실행해 예시 질문이 잠깐 보였다 사라지는 현상을 막는다.
  useLayoutEffect(() => {
    clearTurns()
  }, [clearTurns])

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

  // /chat을 호출하고 성공·모호함·에러 세 갈래로 해당 턴의 상태를 갱신하는 공통 로직.
  // confirmedSoFar는 직전 라운드까지 사용자가 확정한 후보들(모호한 이름이
  // 여러 개면 한 번에 하나씩 확정되므로 누적해서 다시 보낸다). 이미 대화 목록에
  // 있는 turnId를 갱신하므로 후보를 골라 재요청해도 새 턴이 추가되지 않는다.
  const runChatQuery = async (
    turnId: string,
    question: string,
    confirmedSoFar: ConfirmedEntity[],
  ) => {
    updateTurn(turnId, { status: 'loading' })
    try {
      const response = await sendChatQuery(
        question,
        confirmedSoFar.length === 0 ? undefined : confirmedSoFar,
      )
      setPendingClarification(null)
      updateTurn(turnId, { status: 'success', result: toDisplayResult(response) })
      refreshHistory()
    } catch (err) {
      if (err instanceof ClarificationNeededError) {
        setPendingClarification({
          turnId,
          query: question,
          confirmedSoFar,
          message: err.message,
          candidates: err.candidates,
          lookupName: err.lookupName,
        })
        updateTurn(turnId, { status: 'clarify' })
        return
      }
      setPendingClarification(null)
      updateTurn(turnId, {
        status: 'error',
        errorMessage: err instanceof ChatError ? err.message : '질의 처리 중 오류가 발생했습니다',
      })
    }
  }

  // 질문 제출: 새 턴을 대화 목록 끝에 추가하고 /chat을 호출해 결과·이력을 갱신한다
  const handleSubmit = async () => {
    const question = queryText.trim()
    if (!question) return
    setQueryText('')
    const turn: ConversationTurn = {
      id: crypto.randomUUID(),
      query: question,
      status: 'loading',
      result: null,
      errorMessage: '',
    }
    addTurn(turn)
    await runChatQuery(turn.id, question, [])
  }

  // 모호한 이름 후보 중 하나를 선택하면 확정 목록에 더해 같은 턴으로 재요청한다.
  const handleSelectCandidate = async (candidate: AmbiguousCandidate) => {
    if (!pendingClarification) return
    await runChatQuery(pendingClarification.turnId, pendingClarification.query, [
      ...pendingClarification.confirmedSoFar,
      { entity: candidate.entity, forName: pendingClarification.lookupName },
    ])
  }

  // 후보 선택을 취소하면 답변을 얻지 못한 턴이므로 대화 목록에서 아예 지운다
  const handleCancelClarification = () => {
    if (!pendingClarification) return
    removeTurn(pendingClarification.turnId)
    setPendingClarification(null)
  }

  // 대화기록 목록에서 항목을 클릭하면 재호출 없이 저장된 내용을 대화 끝에 새 턴으로 이어붙인다
  const handleSelectHistoryItem = (item: HistoryEntry) => {
    addTurn({
      id: crypto.randomUUID(),
      query: item.query,
      status: 'success',
      result: toDisplayResult(item),
      errorMessage: '',
    })
  }

  // 대화기록 항목을 삭제하고 사이드바 목록을 갱신한다(현재 보고 있는 대화는 건드리지 않는다)
  const handleDeleteHistoryItem = async (item: HistoryEntry) => {
    try {
      await deleteHistory(item.id)
      refreshHistory()
    } catch (err) {
      console.error('deleteHistory failed:', err)
    }
  }

  // 홈으로 돌아갈 때는 이전 대화의 잔여 UI 상태(쿼리 패널)도 함께 초기화해서
  // 다음 대화에 이전 상태가 그대로 남지 않도록 한다.
  const handleNavigateHome = () => {
    clearTurns()
    setQueryText('')
    setPendingClarification(null)
    if (queryPanelCollapsed) toggleQueryPanelCollapsed()
  }

  const queryInputBar = (
    <QueryInputBar value={queryText} onChange={setQueryText} onSubmit={handleSubmit} />
  )

  // 우측 생성 쿼리 패널은 대화 중 가장 최근에 성공한 턴을 기준으로 보여준다.
  // 새 질문이 로딩 중이어도 직전 답변의 패널은 그대로 유지된다.
  const latestResultTurn = [...turns].reverse().find((t) => t.status === 'success' && t.result)

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
          onDeleteHistoryItem={handleDeleteHistoryItem}
          onNavigateDashboard={() => navigate('/dashboard')}
          onNavigateChat={handleNavigateHome}
        />
        <main className="flex flex-1 flex-col overflow-hidden">
          {turns.length === 0 ? (
            <div className="flex flex-1 flex-col items-center justify-center gap-6 overflow-y-auto p-6">
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
            </div>
          ) : (
            <>
              <div className="flex flex-1 flex-col gap-6 overflow-y-auto p-6">
                {turns.map((turn) => (
                  <div
                    key={turn.id}
                    className="flex flex-col gap-3 border-b border-border pb-6 last:border-b-0 last:pb-0"
                  >
                    <p className="text-[13.5px] font-semibold text-text">{turn.query}</p>
                    {turn.status === 'loading' && (
                      <div className="flex items-center gap-2 text-text-muted">
                        <Loader2 className="size-4 animate-spin" />
                        <p className="text-sm">답변을 생성하는 중입니다…</p>
                      </div>
                    )}
                    {turn.status === 'error' && (
                      <p className="text-sm text-fail">{turn.errorMessage}</p>
                    )}
                    {turn.status === 'clarify' &&
                      pendingClarification &&
                      pendingClarification.turnId === turn.id && (
                        <ClarificationPrompt
                          message={pendingClarification.message}
                          candidates={pendingClarification.candidates}
                          onSelect={handleSelectCandidate}
                          onCancel={handleCancelClarification}
                        />
                      )}
                    {turn.status === 'success' && turn.result && (
                      <div className="flex flex-col gap-4">
                        <NaturalLanguageAnswerBox
                          key={`answer-${turn.id}`}
                          answer={turn.result.answer}
                          visualization={turn.result.visualization}
                          hasGraphResult={turn.result.hasGraphResult}
                          graphRows={turn.result.graphRows}
                          graphError={turn.result.graphError}
                          graphEmptyReason={turn.result.graphEmptyReason}
                        />
                        <ResultEvidencePanel key={turn.id} {...turn.result} />
                      </div>
                    )}
                  </div>
                ))}
              </div>
              <div className="border-t border-border p-4">
                <div className="mx-auto w-full max-w-2xl">{queryInputBar}</div>
              </div>
            </>
          )}
        </main>
        {latestResultTurn?.result &&
        (latestResultTurn.result.sql ||
          latestResultTurn.result.cypher ||
          latestResultTurn.result.sqlAttempts.length > 0 ||
          latestResultTurn.result.cypherAttempts.length > 0) ? (
          <GeneratedQueryPanel
            queries={[
              ...(latestResultTurn.result.sql
                ? [
                    {
                      label: '생성된 SQL',
                      language: 'sql' as const,
                      query: latestResultTurn.result.sql,
                    },
                  ]
                : []),
              ...(latestResultTurn.result.cypher
                ? [
                    {
                      label: '생성된 Cypher',
                      language: 'cypher' as const,
                      query: latestResultTurn.result.cypher,
                    },
                  ]
                : []),
            ]}
            sqlAttempts={attemptsToSteps('sql', latestResultTurn.result.sqlAttempts)}
            cypherAttempts={attemptsToSteps('cypher', latestResultTurn.result.cypherAttempts)}
            collapsed={queryPanelCollapsed}
            onToggleCollapsed={toggleQueryPanelCollapsed}
          />
        ) : null}
      </div>
    </div>
  )
}
