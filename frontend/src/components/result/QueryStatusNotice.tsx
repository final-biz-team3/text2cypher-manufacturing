import type { DisplayResult } from '@/types/query'

const labels = {
  answered: '조회 완료',
  empty: '조회 완료 · 해당 조건의 데이터 없음',
  clarification: '확인이 필요합니다',
  unsupported: '현재 지원하지 않는 조회 방식',
  unanswerable: '제공된 데이터로 확인할 수 없는 요청',
  unverified: '답변 미확정',
  blocked: '데이터 변경 요청 차단',
  error: '시스템 처리 오류',
}

export function QueryStatusNotice({ status }: Pick<DisplayResult, 'status'>) {
  if (!status) return null
  return (
    <p
      role="status"
      className="rounded-md border border-border bg-panel px-4 py-3 text-sm text-text"
    >
      {labels[status]}
    </p>
  )
}
