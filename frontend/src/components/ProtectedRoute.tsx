import { Navigate } from 'react-router-dom'
import { useAuthStore } from '@/store/useAuthStore'

interface Props {
  children: React.ReactNode
}

// 인증되지 않은 사용자를 로그인 화면으로 보낸다
export function ProtectedRoute({ children }: Props) {
  const status = useAuthStore((s) => s.status)

  if (status === 'idle' || status === 'loading') {
    return <div className="flex h-screen items-center justify-center text-text-muted">로딩 중…</div>
  }
  if (status === 'unauthenticated') {
    return <Navigate to="/login" replace />
  }
  return <>{children}</>
}
