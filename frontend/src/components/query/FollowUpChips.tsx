import { Button } from '@/components/ui/button'

interface FollowUpChipsProps {
  questions: string[]
  onSelect: (question: string) => void
}

// 답변 하단에 표시되는 후속 질문 칩 목록. 클릭 시 해당 질문을 입력창에 채워준다
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
