import { describe, expect, it } from 'vitest'
import { z } from 'zod'
import { HistoryEntrySchema } from './schemas'

describe('HistoryEntrySchema', () => {
  it('parses a history list containing a legacy scatter visualization record without throwing', () => {
    // scatter는 더 이상 새로 생성되지 않지만, 과거에 저장된 대화기록에는
    // 이 타입이 남아있을 수 있다. fetchHistory()는 배열 전체를 한 번에
    // strict 파싱하므로(history.ts), 레코드 하나라도 스키마와 안 맞으면
    // 이력 목록 전체 조회가 깨진다 - 이 회귀를 막는 테스트다.
    const rows = [
      {
        id: 1,
        username: 'kim.quality',
        query:
          '공급업체 Allenson Cycles가 공급을 중단하면 영향을 받는 부품과 완제품, 각 부품의 현재 재고를 알려줘.',
        final_answer: '요청하신 조건에 맞는 항목을 확인했습니다.',
        sql_query: null,
        cypher_query: 'MATCH (s:Supplier)-[:SUPPLIES]->(c:Product) RETURN c',
        sql_result: null,
        graph_result: { result: [], error: null, attempts: [], empty_reason: null },
        // 예전 산점도 응답 모양 그대로(xLabel/yLabel/points는 지금 스키마에
        // 없는 필드지만 zod object는 기본적으로 알 수 없는 키를 무시한다).
        visualization: {
          type: 'scatter',
          title: null,
          xLabel: 'depth',
          yLabel: '실제재고',
          points: [{ x: 2, y: 780 }],
        },
        created_at: '2026-09-08T00:00:00Z',
      },
    ]

    expect(() => z.array(HistoryEntrySchema).parse(rows)).not.toThrow()
  })
})
