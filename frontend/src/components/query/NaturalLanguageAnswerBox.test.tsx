import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import { NaturalLanguageAnswerBox } from './NaturalLanguageAnswerBox'

describe('NaturalLanguageAnswerBox', () => {
  it('renders paragraphs, GFM lists, emphasis, and tables', () => {
    const html = renderToStaticMarkup(
      <NaturalLanguageAnswerBox
        answer={`**핵심 결과**입니다.

- 절단
- 용접

| 부품 | 수량 |
| --- | ---: |
| 프레임 | 2 |`}
      />,
    )

    expect(html).toContain('AI 정리 답변')
    expect(html).toContain('조회된 데이터만 근거로 정리했습니다.')
    expect(html).toContain('<strong>핵심 결과</strong>')
    expect(html).toContain('<ul')
    expect(html).toContain('<table')
    expect(html).toContain('overflow-x-auto')
    expect(html).toContain('<td')
  })

  it('does not execute raw HTML or expose model-provided links', () => {
    const html = renderToStaticMarkup(
      <NaturalLanguageAnswerBox
        answer={'안전한 답변 <script>alert("x")</script> [외부 링크](https://example.com)'}
      />,
    )

    expect(html).not.toContain('<script')
    expect(html).not.toContain('href=')
    expect(html).toContain('<span>외부 링크</span>')
  })
})
