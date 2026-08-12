interface FollowUpChipsProps {
  questions: string[]
  onSelect: (question: string) => void
}

export function FollowUpChips({ questions, onSelect }: FollowUpChipsProps) {
  return (
    <div className="flex flex-wrap gap-2">
      {questions.map((q) => (
        <button
          key={q}
          type="button"
          onClick={() => onSelect(q)}
          className="rounded-full border border-border bg-panel px-3 py-1.5 text-[12px] text-text hover:border-border-strong"
        >
          {q}
        </button>
      ))}
    </div>
  )
}
