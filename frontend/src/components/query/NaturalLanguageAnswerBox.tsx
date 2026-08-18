interface NaturalLanguageAnswerBoxProps {
  answer: string
}

// LLM이 생성한 자연어 답변을 강조 박스로 표시
export function NaturalLanguageAnswerBox({ answer }: NaturalLanguageAnswerBoxProps) {
  return (
    <div className="rounded-lg border border-info bg-accent-bg p-4 text-[13.5px] leading-relaxed text-text">
      {answer}
    </div>
  )
}
