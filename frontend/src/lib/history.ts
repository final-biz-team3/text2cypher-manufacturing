import { z } from 'zod'
import { api } from './api'
import { HistoryEntrySchema, type HistoryEntry } from './schemas'

// 로그인한 사용자의 대화기록을 조회한다(admin은 전체, 일반 사용자는 본인 것만)
export async function fetchHistory(): Promise<HistoryEntry[]> {
  const res = await api.get('/history')
  return z.array(HistoryEntrySchema).parse(res.data)
}

// 대화기록 한 건을 삭제한다(admin은 아무 기록이나, 일반 사용자는 본인 것만 - 서버가 검증)
export async function deleteHistory(id: number): Promise<void> {
  await api.delete(`/history/${id}`)
}
