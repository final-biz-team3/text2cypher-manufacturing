import { Input } from '@/components/ui/input'
import { Button } from '@/components/ui/button'

interface QueryInputBarProps {
  value: string
  onChange: (value: string) => void
  onSubmit: () => void
}

// 자연어 질문 입력창 + 제출 버튼. idle/success 화면에서 공통으로 재사용된다
export function QueryInputBar({ value, onChange, onSubmit }: QueryInputBarProps) {
  return (
    <form
      className="flex gap-2"
      onSubmit={(e) => {
        e.preventDefault()
        onSubmit()
      }}
    >
      <Input
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder="예: LOT-2041에서 발생한 불량의 원인 경로를 찾아줘"
        className="h-[46px] flex-1 rounded-lg border-border-strong px-4 shadow-sm md:text-base"
      />
      <Button type="submit" className="h-[46px] shrink-0 whitespace-nowrap rounded-lg px-5">
        질문하기
      </Button>
    </form>
  )
}
