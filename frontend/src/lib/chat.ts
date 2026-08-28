import { AxiosError } from 'axios'
import { api } from './api'
import {
  ApiErrorSchema,
  ChatResponseSchema,
  ClarificationNeededSchema,
  type AmbiguousCandidate,
  type ChatResponse,
} from './schemas'

export class ChatError extends Error {}

// 엔티티 이름이 모호해 사용자가 후보 중 하나를 골라야 할 때 던진다
export class ClarificationNeededError extends Error {
  candidates: AmbiguousCandidate[]

  constructor(message: string, candidates: AmbiguousCandidate[]) {
    super(message)
    this.candidates = candidates
  }
}

// 자연어 질의를 /chat에 보내고 결과를 반환한다.
// confirmedEntity는 이전에 ENTITY_AMBIGUOUS 응답에서 사용자가 고른 후보(들)를
// 되돌려보낼 때 쓴다 - 모호한 이름이 여러 개면 확정된 후보를 배열로 누적해서 보낸다.
export async function sendChatQuery(
  query: string,
  confirmedEntity?: AmbiguousCandidate['entity'] | AmbiguousCandidate['entity'][],
): Promise<ChatResponse> {
  let data: unknown
  try {
    const res = await api.post('/chat', { query, confirmed_entity: confirmedEntity ?? null })
    data = res.data
  } catch (err) {
    const axiosErr = err as AxiosError
    const parsed = ApiErrorSchema.safeParse(axiosErr.response?.data)
    throw new ChatError(parsed.success ? parsed.data.message : '질의 처리 중 오류가 발생했습니다')
  }

  const ambiguous = ClarificationNeededSchema.safeParse(data)
  if (ambiguous.success) {
    throw new ClarificationNeededError(ambiguous.data.message, ambiguous.data.candidates)
  }
  return ChatResponseSchema.parse(data)
}
