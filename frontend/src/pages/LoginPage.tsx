import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { TopBar } from '@/components/layout/TopBar'
import { LoginAsidePanel } from '@/components/layout/LoginAsidePanel'
import { useAuthStore } from '@/store/useAuthStore'
import { useLoginPrefsStore } from '@/store/useLoginPrefsStore'
import { AuthError } from '@/lib/api'

// 로그인 화면: 아이디/비밀번호 입력 후 인증하고 성공 시 대시보드로 이동한다
export function LoginPage() {
  const navigate = useNavigate()
  const login = useAuthStore((s) => s.login)
  const { rememberId, savedUsername, setRememberId, setSavedUsername } = useLoginPrefsStore()

  const [username, setUsername] = useState(savedUsername)
  const [password, setPassword] = useState('')
  const [pwVisible, setPwVisible] = useState(false)
  const [errorMessage, setErrorMessage] = useState<string | null>(null)
  const [isSubmitting, setIsSubmitting] = useState(false)

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!username || !password) return
    setIsSubmitting(true)
    setErrorMessage(null)
    try {
      await login(username, password)
      setSavedUsername(rememberId ? username : '')
      navigate('/')
    } catch (err) {
      setErrorMessage(err instanceof AuthError ? err.message : '로그인 중 오류가 발생했습니다')
    } finally {
      setIsSubmitting(false)
    }
  }

  const invalidRing = errorMessage ? 'border-fail' : 'border-border-strong'

  return (
    <div className="flex h-screen flex-col overflow-hidden bg-bg">
      <TopBar
        connected={false}
        connectionEndpoint="bolt://prod-kg-01"
        readOnly
        onNavigateHome={() => {}}
      />
      <div className="flex min-h-0 flex-1">
        <main className="flex min-w-0 flex-1 items-center justify-center p-6">
          <form onSubmit={handleSubmit} className="flex w-[400px] max-w-full flex-col gap-4">
            <div>
              <h1 className="text-xl font-bold text-text">로그인</h1>
              <p className="mt-1 text-[13px] leading-normal text-text-muted">
                사내 계정으로 접속하세요. 조회 권한만 부여되며 그래프 데이터는 변경되지 않습니다.
              </p>
            </div>

            {errorMessage ? (
              <div
                role="alert"
                className="rounded-md border border-fail bg-accent-bg px-3.5 py-2.5"
              >
                <div className="text-[12.5px] font-bold text-fail">인증 실패 · {errorMessage}</div>
              </div>
            ) : null}

            <div className="flex flex-col gap-3">
              <div>
                <label
                  htmlFor="username"
                  className="mb-1.5 block text-[11.5px] font-bold uppercase text-text-faint"
                >
                  아이디
                </label>
                <input
                  id="username"
                  autoComplete="username"
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                  placeholder="예: kim.quality"
                  aria-invalid={Boolean(errorMessage)}
                  className={`w-full rounded-md border ${invalidRing} bg-panel px-3.5 py-2.5 font-mono text-sm text-text outline-none focus:border-info`}
                />
              </div>

              <div>
                <label
                  htmlFor="password"
                  className="mb-1.5 block text-[11.5px] font-bold uppercase text-text-faint"
                >
                  비밀번호
                </label>
                <div className="relative flex">
                  <input
                    id="password"
                    type={pwVisible ? 'text' : 'password'}
                    autoComplete="current-password"
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    placeholder="비밀번호 입력"
                    aria-invalid={Boolean(errorMessage)}
                    className={`min-w-0 flex-1 rounded-md border ${invalidRing} bg-panel py-2.5 pl-3.5 pr-[74px] text-sm text-text outline-none focus:border-info`}
                  />
                  <button
                    type="button"
                    onClick={() => setPwVisible((v) => !v)}
                    className="absolute inset-y-1.5 right-1.5 rounded-md border border-border bg-panel-2 px-2.5 text-[11px] text-text-muted"
                  >
                    {pwVisible ? '숨기기' : '표시'}
                  </button>
                </div>
              </div>
            </div>

            <label className="flex cursor-pointer items-center gap-2 text-[12.5px] text-text">
              <input
                type="checkbox"
                checked={rememberId}
                onChange={(e) => setRememberId(e.target.checked)}
                className="size-4 accent-info"
              />
              이 기기에서 아이디 기억
            </label>

            <button
              type="submit"
              disabled={isSubmitting || !username || !password}
              className="w-full rounded-md bg-info py-3 text-sm font-semibold text-white disabled:opacity-60"
            >
              {isSubmitting ? '인증 중…' : '로그인'}
            </button>
          </form>
        </main>
        <LoginAsidePanel />
      </div>
    </div>
  )
}
