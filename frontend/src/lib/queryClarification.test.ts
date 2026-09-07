import { describe, expect, it } from 'vitest'
import { clarifyQuery } from './queryClarification'
import { ChatResponseSchema } from './schemas'

describe('query clarification', () => {
  it('preserves original conditions and successive explicit answers', () => {
    const first = clarifyQuery('분기별 생산량을 비교해줘', '어느 연도인가요?', ' 2024 ')
    const next = clarifyQuery(first, '어느 공장인가요?', '제1공장')
    expect(next).toContain('분기별 생산량을 비교해줘')
    expect(next).toContain('사용자 확인: 2024')
    expect(next).toContain('사용자 확인: 제1공장')
  })
  it('accepts both old responses and optional typed clarification', () => {
    expect(ChatResponseSchema.parse({ query: 'q' }).clarification).toBeUndefined()
    expect(
      ChatResponseSchema.parse({ query: 'q', clarification: { question: '기준?', options: [] } })
        .clarification?.options,
    ).toEqual([])
    expect(
      ChatResponseSchema.safeParse({ query: 'q', clarification: { question: '', options: [] } })
        .success,
    ).toBe(false)
  })
})
