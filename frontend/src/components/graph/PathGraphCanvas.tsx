import { Button } from '@/components/ui/button'

export function PathGraphCanvas() {
  return (
    <div className="relative flex h-[240px] items-center justify-center rounded-lg border border-border bg-panel-2">
      <p className="text-[12.5px] text-text-faint">
        그래프 시각화 영역 (react-force-graph-2d 연동 예정)
      </p>
      <div className="absolute top-2 right-2 flex gap-1">
        {['+', '-', '맞춤', '리셋'].map((label) => (
          <Button
            key={label}
            type="button"
            variant="outline"
            size="sm"
            disabled
            className="h-6 px-2 text-[11px]"
          >
            {label}
          </Button>
        ))}
      </div>
    </div>
  )
}
