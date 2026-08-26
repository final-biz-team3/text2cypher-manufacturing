import { z } from 'zod'
import { api } from './api'
import { HistoryEntrySchema, type HistoryEntry } from './schemas'

// 로그인한 사용자의 대화기록을 조회한다(admin은 전체, 일반 사용자는 본인 것만)
export async function fetchHistory(): Promise<HistoryEntry[]> {
  const res = await api.get('/history')
  return z.array(HistoryEntrySchema).parse(res.data)
}
