import type { ChatResponse, HistoryEntry } from './schemas'
import type { DisplayResult, ResultColumn } from '@/types/query'

const LEGACY_RAW_ANSWER = /^\s*(?:COMPOSED|GRAPH|SQL):\s*/i

function toDisplayAnswer(answer: string | null | undefined): string {
  if (!answer) return '답변을 생성하지 못했습니다.'
  if (LEGACY_RAW_ANSWER.test(answer)) {
    return '이 기록은 AI 정리 답변 기능이 적용되기 전에 저장된 결과입니다. 동일한 질문을 다시 실행하면 현재 LLM이 조회 결과를 정리해 답변합니다.'
  }
  return answer
}

// /chat 응답과 저장된 대화기록을 동일한 화면 모델로 정규화한다.
export function toDisplayResult(response: ChatResponse | HistoryEntry): DisplayResult {
  const rowsRaw = response.sql_result?.result ?? response.graph_result?.result ?? []
  const columns: ResultColumn[] =
    rowsRaw.length > 0 ? Object.keys(rowsRaw[0]).map((key) => ({ key, label: key })) : []
  const rows = rowsRaw.map((row) =>
    Object.fromEntries(
      Object.entries(row).map(([key, value]) => [key, value == null ? '' : String(value)]),
    ),
  )

  return {
    query: response.query,
    answer: toDisplayAnswer(response.final_answer),
    sql: response.sql_query ?? null,
    cypher: response.cypher_query ?? null,
    columns,
    rows,
    hasGraphResult: response.graph_result != null,
    graphRows: response.graph_result?.result ?? [],
    graphError: response.graph_result?.error ?? null,
    graphEmptyReason: response.graph_result?.empty_reason ?? null,
    sqlAttempts: response.sql_result?.attempts ?? [],
    cypherAttempts: response.graph_result?.attempts ?? [],
    visualization: response.visualization ?? null,
  }
}
