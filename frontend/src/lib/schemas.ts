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
})

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
