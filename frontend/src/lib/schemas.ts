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

export const RetryAttemptSchema = z.object({
  query: z.string(),
  error: z.string().nullable(),
})

export const QueryOutcomeSchema = z
  .object({
    result: z.array(z.record(z.string(), z.unknown())).nullable(),
    error: z.string().nullable(),
    attempts: z.array(RetryAttemptSchema),
    empty_reason: z.string().nullable(),
  })
  .nullable()

export const ChatResponseSchema = z.object({
  query: z.string(),
  sql_query: z.string().nullable().optional(),
  cypher_query: z.string().nullable().optional(),
  sql_result: QueryOutcomeSchema.optional(),
  graph_result: QueryOutcomeSchema.optional(),
  final_answer: z.string().nullable().optional(),
})
export type ChatResponse = z.infer<typeof ChatResponseSchema>

export const ApiErrorSchema = z.object({
  message: z.string(),
})

export const AmbiguousCandidateSchema = z.object({
  id: z.union([z.string(), z.number()]),
  name: z.string(),
  entityType: z.string(),
  score: z.number(),
  entity: z.record(z.string(), z.unknown()),
})
export type AmbiguousCandidate = z.infer<typeof AmbiguousCandidateSchema>

export const ClarificationNeededSchema = z.object({
  code: z.literal('ENTITY_AMBIGUOUS'),
  message: z.string(),
  candidates: z.array(AmbiguousCandidateSchema),
  // 사용자가 고른 후보를 confirmed_entity로 되돌려보낼 때 함께 실어 보내는
  // 상관관계 키(이번에 모호했던 원문 그대로의 추출 이름). 이게 없으면 재확인
  // 요청의 confirmed_entity가 "이번 모호함 질문"에 대한 응답인지, 이름이
  // 우연히 비슷한 별개의 새 대상인지 서버가 구분할 수 없다.
  lookupName: z.string(),
})

// 사용자가 유사도 후보를 선택해 확정한 엔티티. forName은 이 확정값이 어떤
// 모호함 질문(candidates가 나왔던 원본 추출 이름)에 대한 응답인지 표시한다.
export const ConfirmedEntitySchema = z.object({
  entity: z.record(z.string(), z.unknown()),
  forName: z.string(),
})
export type ConfirmedEntity = z.infer<typeof ConfirmedEntitySchema>

export const HealthSchema = z.object({
  status: z.string(),
  env: z.string(),
  neo4j: z.object({ status: z.string(), detail: z.string().optional() }),
  postgres: z.object({ status: z.string(), detail: z.string().optional() }),
})
export type Health = z.infer<typeof HealthSchema>

export const HistoryEntrySchema = z.object({
  id: z.number(),
  username: z.string(),
  query: z.string(),
  final_answer: z.string().nullable(),
  sql_query: z.string().nullable(),
  cypher_query: z.string().nullable(),
  sql_result: QueryOutcomeSchema,
  graph_result: QueryOutcomeSchema,
  created_at: z.string(),
})
export type HistoryEntry = z.infer<typeof HistoryEntrySchema>
