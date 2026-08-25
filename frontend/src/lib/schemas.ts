import { z } from 'zod'

export const LoginRequestSchema = z.object({
  username: z.string().min(1, '아이디를 입력하세요'),
  password: z.string().min(1, '비밀번호를 입력하세요'),
})
export type LoginRequest = z.infer<typeof LoginRequestSchema>

export const CurrentUserSchema = z.object({
  username: z.string(),
  role: z.enum(['admin', 'user']),
})
export type CurrentUser = z.infer<typeof CurrentUserSchema>

export const LoginErrorSchema = z.object({
  detail: z.string(),
})
export type LoginError = z.infer<typeof LoginErrorSchema>
