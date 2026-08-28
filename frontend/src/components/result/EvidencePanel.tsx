import type { ReactNode } from 'react'

interface EvidencePanelProps {
  open: boolean
  onToggle: () => void
  children: ReactNode
}

// "답변 근거 보기" 아코디언. 자가수정 타임라인 등 근거 콘텐츠를 접었다 펼 수 있게 감싼다
export function EvidencePanel({ open, onToggle, children }: EvidencePanelProps) {
  return (
    <div className="rounded-lg border border-border bg-panel">
      <button
        type="button"
        onClick={onToggle}
        className="flex w-full items-center justify-between px-4 py-2.5 text-left"
        aria-expanded={open}
        aria-label={open ? '근거 패널 접기' : '근거 패널 펼치기'}
      >
        <span className="text-[12.5px] font-semibold text-text">답변 근거 보기</span>
        <span className="text-text-faint">{open ? '▾' : '▸'}</span>
      </button>
      {open ? (
        <div className="flex flex-col gap-3 border-t border-border p-4">{children}</div>
      ) : null}
    </div>
  )
}
