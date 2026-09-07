import { useState } from 'react'

type Props = {
  question: string
  options: string[]
  onAnswer: (answer: string) => void
  onCancel: () => void
}

export function QueryClarification({ question, options, onAnswer, onCancel }: Props) {
  const [answer, setAnswer] = useState('')
  return (
    <section className="rounded-xl border border-border bg-panel p-5" aria-label="조회 기준 확인">
      <h2 className="mb-3 font-semibold text-text">{question}</h2>
      <div className="flex flex-wrap gap-2">
        {options.map((option) => (
          <button
            type="button"
            key={option}
            onClick={() => onAnswer(option)}
            className="rounded-md border border-border px-3 py-2 text-sm text-text hover:border-border-strong"
          >
            {option}
          </button>
        ))}
      </div>
      <form
        className="mt-4 flex flex-wrap gap-2"
        onSubmit={(event) => {
          event.preventDefault()
          if (answer.trim()) onAnswer(answer.trim())
        }}
      >
        <input
          aria-label="조회 기준 직접 입력"
          value={answer}
          onChange={(event) => setAnswer(event.target.value)}
          className="min-w-0 flex-1 basis-full rounded-md border border-border bg-panel px-3 py-2 text-text sm:basis-0"
          placeholder="필요한 기준을 직접 적어 주세요"
        />
        <button
          type="submit"
          disabled={!answer.trim()}
          className="rounded-md border border-border px-3 py-2 text-text disabled:opacity-40"
        >
          확인
        </button>
        <button type="button" onClick={onCancel} className="px-3 py-2 text-text-muted">
          취소
        </button>
      </form>
    </section>
  )
}
