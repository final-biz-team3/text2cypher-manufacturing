import { AxiosError } from 'axios'
import { api } from './api'
import {
  ApiErrorSchema,
  ChatResponseSchema,
  ClarificationNeededSchema,
  type AmbiguousCandidate,
  type ChatResponse,
  type ConfirmedEntity,
} from './schemas'

export class ChatError extends Error {}

const configuredChatTimeout = Number(import.meta.env.VITE_CHAT_TIMEOUT_MS ?? 60_000)
const CHAT_TIMEOUT_MS =
  Number.isFinite(configuredChatTimeout) && configuredChatTimeout > 0
    ? configuredChatTimeout
    : 60_000

// 엔티티 이름이 모호해 사용자가 후보 중 하나를 골라야 할 때 던진다
export class ClarificationNeededError extends Error {
  candidates: AmbiguousCandidate[]
  // 사용자의 선택을 confirmed_entity로 되돌려보낼 때 ConfirmedEntity.forName에
  // 그대로 실어 보내는 상관관계 키 - resolve_entity.ts의 사용처 참고.
  lookupName: string

  constructor(message: string, candidates: AmbiguousCandidate[], lookupName: string) {
    super(message)
    this.candidates = candidates
    this.lookupName = lookupName
  }
}

// 자연어 질의를 /chat에 보내고 결과를 반환한다.
// confirmedEntities는 이전에 ENTITY_AMBIGUOUS 응답에서 사용자가 고른 후보(들)를
// 되돌려보낼 때 쓴다 - 모호한 이름이 여러 개면 확정된 후보를 배열로 누적해서 보낸다.
// 각 항목의 forName은 그 후보가 어떤 모호함 질문에 대한 응답인지 표시해, 서버가
// "이번 재확인"과 "이름이 우연히 비슷한 별개의 새 대상"을 구분할 수 있게 한다.
export async function sendChatQuery(
  query: string,
  confirmedEntities?: ConfirmedEntity[],
): Promise<ChatResponse> {
  let data: unknown
  try {
    const res = await api.post(
      '/chat',
      { query, confirmed_entity: confirmedEntities ?? null },
      { timeout: CHAT_TIMEOUT_MS },
    )
    data = res.data
  } catch (err) {
    const axiosErr = err as AxiosError
    const parsed = ApiErrorSchema.safeParse(axiosErr.response?.data)
    throw new ChatError(parsed.success ? parsed.data.message : '질의 처리 중 오류가 발생했습니다')
  }

  const ambiguous = ClarificationNeededSchema.safeParse(data)
  if (ambiguous.success) {
    throw new ClarificationNeededError(
      ambiguous.data.message,
      ambiguous.data.candidates,
      ambiguous.data.lookupName,
    )
  }
  return ChatResponseSchema.parse(data)
}
