import { describe, expect, it } from 'vitest'
import { formatQueryError } from './formatQueryError'

describe('formatQueryError', () => {
  it('formats a raw syntax error with a translated message body and location', () => {
    const raw =
      "{neo4j_code: Neo.ClientError.Statement.SyntaxError} {message: Invalid input 'NOT': expected an expression or ')' (line 6, column 33 (offset: 306))\n" +
      '" WHERE nodes(p)[i].productId NOT IN [n IN nodes(p)[0..i] | n.productId])"\n' +
      '^} {gql_status: 50N42} {gql_status_description: error: general processing exception - unexpected error. Unexpected error has occurred. See debug log for details.}'

    expect(formatQueryError(raw)).toBe(
      "Cypher 문법 오류로 실패했습니다 (입력값 'NOT'을(를) 여기서 사용할 수 없습니다, 6번째 줄 33번째 열).",
    )
  })

  it('describes an empty/early-terminated input as ending early', () => {
    const raw =
      "{neo4j_code: Neo.ClientError.Statement.SyntaxError} {message: Invalid input '': expected an expression (line 1, column 11 (offset: 10))\n" +
      '"RETURN 1 +"\n' +
      '           ^}'

    expect(formatQueryError(raw)).toBe(
      'Cypher 문법 오류로 실패했습니다 (입력이 예상보다 일찍 끝났습니다, 1번째 줄 11번째 열).',
    )
  })

  it('falls back to the category only when the message body does not match a known pattern', () => {
    const raw =
      '{neo4j_code: Neo.ClientError.Schema.ConstraintValidationFailed} {message: Node already exists with label X}'
    expect(formatQueryError(raw)).toBe('제약 조건 위반으로 실패했습니다.')
  })

  it('falls back to a generic Korean label for unrecognized neo4j_code categories', () => {
    expect(
      formatQueryError('{neo4j_code: Neo.ClientError.Statement.SomethingElse} {message: whatever}'),
    ).toBe('쿼리 실행 오류로 실패했습니다.')
  })

  it('returns already human-readable Korean messages unchanged', () => {
    expect(formatQueryError('접속 오류가 발생했습니다.')).toBe('접속 오류가 발생했습니다.')
    expect(formatQueryError('스키마에 없는 Label: CAUSED_BY')).toBe(
      '스키마에 없는 Label: CAUSED_BY',
    )
  })

  it('returns strings that do not match the raw driver pattern unchanged', () => {
    expect(formatQueryError('syntax error at or near "NOT"')).toBe('syntax error at or near "NOT"')
  })
})
