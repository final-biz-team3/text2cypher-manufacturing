import { describe, expect, it, vi } from 'vitest'
import { AxiosError, AxiosHeaders } from 'axios'
import { api } from './api'
import { ChatError, ClarificationNeededError, sendChatQuery } from './chat'

vi.mock('./api', () => ({
  api: { post: vi.fn() },
}))

const postMock = vi.mocked(api.post)

describe('sendChatQuery', () => {
  it('returns the parsed response on success', async () => {
    const data = { query: '재고가 부족한 제품을 알려줘' }
    postMock.mockResolvedValueOnce({ data })

    const result = await sendChatQuery('재고가 부족한 제품을 알려줘')

    expect(result).toEqual(data)
    expect(postMock).toHaveBeenCalledWith('/chat', {
      query: '재고가 부족한 제품을 알려줘',
      confirmed_entity: null,
    })
  })

  it('sends confirmed_entity when provided', async () => {
    postMock.mockResolvedValueOnce({ data: { query: 'q' } })

    await sendChatQuery('q', { productId: 680 })

    expect(postMock).toHaveBeenCalledWith('/chat', {
      query: 'q',
      confirmed_entity: { productId: 680 },
    })
  })

  it('throws ClarificationNeededError when the response is ENTITY_AMBIGUOUS', async () => {
    postMock.mockResolvedValueOnce({
      data: {
        code: 'ENTITY_AMBIGUOUS',
        message: '비슷한 이름이 여러 개 있습니다.',
        candidates: [
          {
            id: 1,
            name: 'LL Road Frame',
            entityType: 'product',
            score: 0.9,
            entity: { productId: 1 },
          },
        ],
      },
    })

    const err = await sendChatQuery('그 제품 알려줘').catch((e: unknown) => e)

    expect(err).toBeInstanceOf(ClarificationNeededError)
    const clarification = err as ClarificationNeededError
    expect(clarification.message).toBe('비슷한 이름이 여러 개 있습니다.')
    expect(clarification.candidates).toHaveLength(1)
    expect(clarification.candidates[0].name).toBe('LL Road Frame')
  })

  it('throws ChatError with the server message on a hard error response', async () => {
    const axiosErr = new AxiosError('Request failed', 'ERR_BAD_REQUEST', undefined, undefined, {
      status: 404,
      statusText: 'Not Found',
      headers: new AxiosHeaders(),
      config: { headers: new AxiosHeaders() },
      data: { message: '질의 대상을 찾을 수 없습니다.' },
    })
    postMock.mockRejectedValueOnce(axiosErr)

    const err = await sendChatQuery('없는 제품 알려줘').catch((e: unknown) => e)

    expect(err).toBeInstanceOf(ChatError)
    expect((err as ChatError).message).toBe('질의 대상을 찾을 수 없습니다.')
  })

  it('throws ChatError with a generic message when the error body is unparseable', async () => {
    postMock.mockRejectedValueOnce(new Error('network down'))

    const err = await sendChatQuery('q').catch((e: unknown) => e)

    expect(err).toBeInstanceOf(ChatError)
    expect((err as ChatError).message).toBe('질의 처리 중 오류가 발생했습니다')
  })
})
