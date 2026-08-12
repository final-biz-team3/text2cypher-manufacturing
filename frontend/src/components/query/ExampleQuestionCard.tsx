interface ExampleQuestionCardProps {
  kind: '경로추적' | '집계'
  question: string
  path: { glyph: string; label: string }[]
  onClick: () => void
}

export function ExampleQuestionCard({ kind, question, path, onClick }: ExampleQuestionCardProps) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="flex flex-col gap-2 rounded-[10px] border border-border bg-panel p-3 text-left transition-colors hover:border-border-strong"
    >
      <span className="text-[10px] font-bold uppercase text-info">{kind}</span>
      <span className="line-clamp-2 text-[12.5px] text-text">{question}</span>
      <div className="flex flex-wrap gap-1">
        {path.map((node) => (
          <span
            key={node.label}
            className="flex items-center gap-1 rounded-full bg-panel-2 px-2 py-0.5 text-[10px] text-text-muted"
          >
            <span className="flex size-[11px] items-center justify-center rounded-full bg-info text-[7px] font-bold text-white">
              {node.glyph}
            </span>
            {node.label}
          </span>
        ))}
      </div>
    </button>
  )
}
