import type { AmbiguousCandidate } from '@/lib/schemas'

interface ClarificationPromptProps {
  message: string
  candidates: AmbiguousCandidate[]
  onSelect: (candidate: AmbiguousCandidate) => void
  onCancel: () => void
}

// 이름이 모호할 때 후보 목록을 보여주고 사용자가 하나를 고르게 한다
export function ClarificationPrompt({
  message,
  candidates,
  onSelect,
  onCancel,
}: ClarificationPromptProps) {
  return (
    <div className="w-full">
      <p className="mb-2 text-sm text-text">{message}</p>
      <ul className="flex flex-col gap-1.5">
        {candidates.map((candidate) => (
          <li key={String(candidate.id)}>
            <button
              type="button"
              onClick={() => onSelect(candidate)}
              className="flex w-full items-center justify-between rounded-md border border-border bg-panel px-3 py-2 text-left text-[12.5px] text-text transition-colors hover:border-border-strong"
            >
              <span>{candidate.name}</span>
              <span className="text-[11px] text-text-faint">{candidate.entityType}</span>
            </button>
          </li>
        ))}
      </ul>
      <button
        type="button"
        onClick={onCancel}
        className="mt-3 text-[12.5px] text-text-muted underline-offset-2 hover:underline"
      >
        취소
      </button>
    </div>
  )
}
