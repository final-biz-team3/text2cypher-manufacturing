import type { SelfCorrectionStep } from '@/types/query'

const STATUS_CLASS: Record<SelfCorrectionStep['status'], string> = {
  success: 'bg-success',
  fail: 'bg-fail',
  warn: 'bg-warn',
}

interface SelfCorrectionTimelineProps {
  steps: SelfCorrectionStep[]
}

export function SelfCorrectionTimeline({ steps }: SelfCorrectionTimelineProps) {
  return (
    <ol className="flex flex-col gap-3">
      {steps.map((step) => (
        <li key={step.id} className="flex gap-2">
          <span className={`mt-1 size-2 shrink-0 rounded-full ${STATUS_CLASS[step.status]}`} />
          <div className="flex flex-col gap-0.5">
            <div className="flex items-baseline gap-2">
              <span className="text-[12.5px] font-semibold text-text">{step.title}</span>
              <span className="font-mono text-[11px] text-text-faint">{step.elapsedMs}ms</span>
            </div>
            <p className="text-[11px] text-text-muted">{step.detail}</p>
          </div>
        </li>
      ))}
    </ol>
  )
}
