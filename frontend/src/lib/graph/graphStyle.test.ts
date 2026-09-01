import { describe, expect, it } from 'vitest'
import { colorForRelationship, labelForRelationship } from './graphStyle'

describe('graph relationship styles', () => {
  it('주요 제조 관계를 한글 라벨과 고유 색으로 표현한다', () => {
    expect(labelForRelationship('REQUIRES_COMPONENT')).toBe('BOM 연결')
    expect(labelForRelationship('SUPPLIES')).toBe('공급')
    expect(colorForRelationship('REQUIRES_COMPONENT')).not.toBe(colorForRelationship('SUPPLIES'))
  })

  it('알 수 없는 관계는 원래 이름과 기본 색을 유지한다', () => {
    expect(labelForRelationship('RELATED_TO')).toBe('RELATED_TO')
    expect(colorForRelationship('RELATED_TO')).toBe('#66717d')
  })
})
