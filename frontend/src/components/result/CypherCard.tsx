import { useState } from 'react'
import { Button } from '@/components/ui/button'

interface CypherCardProps {
  cypher: string
  collapsed: boolean
  onToggleCollapsed: () => void
}

export function CypherCard({ cypher, collapsed, onToggleCollapsed }: CypherCardProps) {
  const [copied, setCopied] = useState(false)

  const handleCopy = () => {
    void navigator.clipboard.writeText(cypher)
    setCopied(true)
    setTimeout(() => setCopied(false), 1400)
  }

  return (
    <div className="rounded-lg border border-border">
      <div className="flex items-center justify-between border-b border-border px-3 py-2">
        <span className="text-[11px] font-semibold uppercase text-text-faint">생성된 Cypher</span>
        <div className="flex gap-1">
          <Button type="button" variant="outline" size="sm" onClick={handleCopy} className="h-6 px-2 text-[11px]">
            {copied ? '복사됨' : '복사'}
          </Button>
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={onToggleCollapsed}
            className="h-6 px-2 text-[11px]"
          >
            {collapsed ? '펴기' : '접기'}
          </Button>
        </div>
      </div>
      {collapsed ? null : (
        <pre className="overflow-x-auto bg-code p-3 font-mono text-[12px] leading-relaxed text-code-text">
          {cypher}
        </pre>
      )}
    </div>
  )
}
