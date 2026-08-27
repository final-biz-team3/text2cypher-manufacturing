import axios, { AxiosError } from 'axios'
import {
  CurrentUserSchema,
  HealthSchema,
  LoginErrorSchema,
  LoginRequestSchema,
  type CurrentUser,
  type Health,
  type LoginRequest,
} from './schemas'

export const api = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL ?? '',
  withCredentials: true,
  timeout: 15_000,
})

export class AuthError extends Error {}

// 로그인 요청을 보내고 성공 시 현재 사용자 정보를 반환한다
export async function login(payload: LoginRequest): Promise<CurrentUser> {
  const body = LoginRequestSchema.parse(payload)
  try {
    const res = await api.post('/auth/login', body)
    return CurrentUserSchema.parse(res.data)
  } catch (err) {
    const axiosErr = err as AxiosError
    if (axiosErr.response?.status === 401) {
      const parsed = LoginErrorSchema.safeParse(axiosErr.response.data)
      throw new AuthError(
        parsed.success ? parsed.data.detail : '아이디 또는 비밀번호가 올바르지 않습니다',
      )
    }
    throw err
  }
}

// 로그아웃 요청을 보낸다
export async function logout(): Promise<void> {
  await api.post('/auth/logout')
}

// 서버·Neo4j·Postgres 연결 상태를 조회한다(인증 불필요)
export async function fetchHealth(): Promise<Health> {
  const res = await api.get('/health')
  return HealthSchema.parse(res.data)
}

// 현재 로그인된 사용자 정보를 조회한다(비로그인 시 401)
export async function fetchMe(): Promise<CurrentUser> {
  const res = await api.get('/auth/me')
  return CurrentUserSchema.parse(res.data)
}
