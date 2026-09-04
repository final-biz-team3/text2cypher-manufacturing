import { useEffect, useState } from 'react'
import { Navigate, useNavigate } from 'react-router-dom'
import {
  fetchFailureReview,
  fetchFailureReviews,
  updateFailureReview,
  type FailureReview,
} from '@/lib/api'
import { AppSidebar } from '@/components/layout/AppSidebar'
import { TopBar } from '@/components/layout/TopBar'
import { useAuthStore } from '@/store/useAuthStore'
import { useHealthStore } from '@/store/useHealthStore'

const STATUSES = ['NEW', 'TRIAGED', 'REPRODUCED', 'FIX_PLANNED', 'FIXED', 'WONT_FIX', 'DUPLICATE']
const CLASSIFICATIONS = [
  'QUESTION_FILTER',
  'ENTITY_RESOLUTION',
  'ROUTING',
  'PLANNING',
  'SQL_GENERATION',
  'CYPHER_GENERATION',
  'SCHEMA_CONTEXT',
  'REPAIR_POLICY',
  'INFRASTRUCTURE',
  'EVALUATION_DATA',
  'OTHER',
]

export function QueryFailureAdmin() {
  const navigate = useNavigate()
  const user = useAuthStore((state) => state.user)
  const logout = useAuthStore((state) => state.logout)
  const neo4jConnected = useHealthStore((state) => state.neo4jConnected)
  const postgresConnected = useHealthStore((state) => state.postgresConnected)
  const [items, setItems] = useState<FailureReview[]>([])
  const [selected, setSelected] = useState<FailureReview | null>(null)
  const [status, setStatus] = useState('')
  const [error, setError] = useState('')

  useEffect(() => {
    let cancelled = false
    fetchFailureReviews(status ? { status } : {})
      .then((result) => {
        if (!cancelled) {
          setItems(result.items)
          setError('')
        }
      })
      .catch(() => {
        if (!cancelled) setError('실패 검토 목록을 불러오지 못했습니다.')
      })
    return () => {
      cancelled = true
    }
  }, [status])

  if (user?.role !== 'admin') return <Navigate to="/chat" replace />

  const openReview = async (reviewId: number) => {
    try {
      setSelected(await fetchFailureReview(reviewId))
      setError('')
    } catch {
      setError('실패 검토 상세를 불러오지 못했습니다.')
    }
  }

  const save = async () => {
    if (!selected) return
    try {
      const updated = await updateFailureReview(selected.review_id, {
        version: selected.version,
        status: selected.status,
        classification: selected.classification,
        assignee: selected.assignee,
        notes: selected.notes,
      })
      setSelected(updated)
      setItems((current) =>
        current.map((item) => (item.review_id === updated.review_id ? updated : item)),
      )
      setError('')
    } catch {
      setError('수정 충돌이 발생했습니다. 항목을 다시 열어 주세요.')
    }
  }

  return (
    <div className="flex h-screen flex-col overflow-hidden bg-bg">
      <TopBar
        connected={neo4jConnected}
        postgresConnected={postgresConnected}
        readOnly
        onNavigateHome={() => navigate('/chat')}
        username={user?.username}
        onLogout={logout}
      />
      <div className="flex min-h-0 flex-1">
        <AppSidebar
          activeSection="admin"
          onNavigateDashboard={() => navigate('/dashboard')}
          onNavigateChat={() => navigate('/chat')}
        />
        <main className="min-w-0 flex-1 overflow-y-auto p-8 text-text">
          <div className="mx-auto max-w-7xl">
            <h1 className="mb-6 text-2xl font-bold">질의 실패 검토</h1>
            <select
              className="mb-4 rounded border p-2"
              value={status}
              onChange={(event) => setStatus(event.target.value)}
            >
              <option value="">모든 상태</option>
              {STATUSES.map((value) => (
                <option key={value}>{value}</option>
              ))}
            </select>
            {error ? <p className="mb-4 text-red-500">{error}</p> : null}
            <div className="overflow-auto rounded border">
              <table className="w-full text-left text-sm">
                <thead>
                  <tr>
                    <th className="p-3">시간</th>
                    <th>경로</th>
                    <th>도구</th>
                    <th>오류</th>
                    <th>상태</th>
                    <th>Request ID</th>
                  </tr>
                </thead>
                <tbody>
                  {items.map((item) => (
                    <tr
                      key={item.review_id}
                      className="cursor-pointer border-t"
                      onClick={() => void openReview(item.review_id)}
                    >
                      <td className="p-3">{new Date(item.created_at).toLocaleString()}</td>
                      <td>{item.route}</td>
                      <td>{item.failed_tool ?? '-'}</td>
                      <td>{item.issue_code}</td>
                      <td>{item.status}</td>
                      <td className="font-mono text-xs">{item.request_id}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {selected ? (
              <section className="mt-6 rounded border p-5">
                <h2 className="mb-3 text-lg font-semibold">#{selected.review_id} 상세</h2>
                <p className="mb-3 whitespace-pre-wrap">{selected.query}</p>
                <pre className="mb-3 overflow-auto rounded bg-black p-3 text-xs text-white">
                  {selected.sql_query ?? selected.cypher_query ?? '생성 쿼리 없음'}
                </pre>
                <div className="grid gap-3 md:grid-cols-3">
                  <select
                    value={selected.status}
                    onChange={(event) => setSelected({ ...selected, status: event.target.value })}
                  >
                    {STATUSES.map((value) => (
                      <option key={value}>{value}</option>
                    ))}
                  </select>
                  <select
                    value={selected.classification ?? ''}
                    onChange={(event) =>
                      setSelected({ ...selected, classification: event.target.value || null })
                    }
                  >
                    <option value="">미분류</option>
                    {CLASSIFICATIONS.map((value) => (
                      <option key={value}>{value}</option>
                    ))}
                  </select>
                  <input
                    placeholder="담당자"
                    value={selected.assignee ?? ''}
                    onChange={(event) =>
                      setSelected({ ...selected, assignee: event.target.value || null })
                    }
                  />
                </div>
                <textarea
                  className="mt-3 min-h-28 w-full rounded border p-2"
                  value={selected.notes ?? ''}
                  onChange={(event) => setSelected({ ...selected, notes: event.target.value })}
                />
                <button
                  className="mt-3 rounded bg-primary px-4 py-2 text-white"
                  onClick={() => void save()}
                >
                  저장
                </button>
              </section>
            ) : null}
          </div>
        </main>
      </div>
    </div>
  )
}
