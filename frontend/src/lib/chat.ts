import { AxiosError } from 'axios'
import { api } from './api'
import { ApiErrorSchema, ChatResponseSchema, type ChatResponse } from './schemas'

export class ChatError extends Error {}

// 자연어 질의를 /chat에 보내고 결과를 반환한다
export async function sendChatQuery(query: string): Promise<ChatResponse> {
  try {
    const res = await api.post('/chat', { query })
    return ChatResponseSchema.parse(res.data)
  } catch (err) {
    const axiosErr = err as AxiosError
    const parsed = ApiErrorSchema.safeParse(axiosErr.response?.data)
    throw new ChatError(parsed.success ? parsed.data.message : '질의 처리 중 오류가 발생했습니다')
  }
}
