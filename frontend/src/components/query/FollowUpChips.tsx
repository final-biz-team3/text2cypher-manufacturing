import { Button } from '@/components/ui/button'

interface FollowUpChipsProps {
  questions: string[]
  onSelect: (question: string) => void
}

export function FollowUpChips({ questions, onSelect }: FollowUpChipsProps) {
  return (
    <div className="flex flex-wrap gap-2">
      {questions.map((q) => (
        <Button
          key={q}
          type="button"
          variant="outline"
          size="sm"
          onClick={() => onSelect(q)}
          className="rounded-full text-[12px] font-normal"
        >
          {q}
        </Button>
      ))}
    </div>
  )
}
