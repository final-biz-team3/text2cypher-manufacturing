import { Navigate } from 'react-router-dom'
import { useAuthStore } from '@/store/useAuthStore'

interface Props {
  children: React.ReactNode
}

// 인증되지 않은 사용자를 로그인 화면으로 보낸다
export function ProtectedRoute({ children }: Props) {
  const status = useAuthStore((s) => s.status)

  if (status === 'unauthenticated') {
    return <Navigate to="/login" replace />
  }
  return <>{children}</>
}
