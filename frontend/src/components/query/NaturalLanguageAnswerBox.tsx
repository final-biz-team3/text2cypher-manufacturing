interface NaturalLanguageAnswerBoxProps {
  answer: string
}

export function NaturalLanguageAnswerBox({ answer }: NaturalLanguageAnswerBoxProps) {
  return (
    <div className="rounded-lg border border-info bg-accent-bg p-4 text-[13.5px] leading-relaxed text-text">
      {answer}
    </div>
  )
}
