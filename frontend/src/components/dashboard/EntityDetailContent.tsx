import { Loader2, MessageSquareText } from 'lucide-react'
import { Button } from '@/components/ui/button'
import type { EntityDetail } from '@/lib/dashboard'
import type { EntitySelection } from '@/store/useDashboardStore'
import { COLUMN_LABELS, entityFromRow, formatDashboardValue } from './dashboardFormatters'

interface EntityDetailContentProps {
  detail: EntityDetail | null
  loading: boolean
  error: string | null
  fallbackProperties?: Record<string, unknown>
  onAsk: (question: string) => void
  onSelectEntity?: (entity: EntitySelection, trigger: HTMLElement) => void
}

function RecordTable({
  rows,
  onSelectEntity,
}: {
  rows: Record<string, unknown>[]
  onSelectEntity?: EntityDetailContentProps['onSelectEntity']
}) {
  if (rows.length === 0)
    return <p className="py-2 text-[11.5px] text-text-faint">등록된 정보가 없습니다.</p>
  const columns = [...new Set(rows.flatMap((row) => Object.keys(row)))].slice(0, 6)
  return (
    <div className="overflow-x-auto rounded-md border border-border">
      <table className="w-full text-[10.5px]">
        <thead className="bg-panel-2 text-text-muted">
          <tr>
            {columns.map((column) => (
              <th key={column} className="px-2 py-1.5 text-left font-medium whitespace-nowrap">
                {COLUMN_LABELS[column] ?? column}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.slice(0, 20).map((row, index) => {
            const related = entityFromRow(row)
            return (
              <tr
                key={index}
                tabIndex={related && onSelectEntity ? 0 : undefined}
                role={related && onSelectEntity ? 'button' : undefined}
                onClick={(event) => related && onSelectEntity?.(related, event.currentTarget)}
                onKeyDown={(event) => {
                  if (related && onSelectEntity && (event.key === 'Enter' || event.key === ' ')) {
                    event.preventDefault()
                    onSelectEntity(related, event.currentTarget)
                  }
                }}
                className={`border-t border-border first:border-t-0 ${related && onSelectEntity ? 'cursor-pointer hover:bg-accent-bg/60 focus-visible:bg-accent-bg focus-visible:outline-none' : ''}`}
              >
                {columns.map((column) => (
                  <td
                    key={column}
                    className="max-w-44 truncate px-2 py-1.5 text-text"
                    title={formatDashboardValue(column, row[column])}
                  >
                    {formatDashboardValue(column, row[column])}
                  </td>
                ))}
              </tr>
            )
          })}
        </tbody>
      </table>
      {rows.length > 20 ? (
        <p className="border-t border-border px-2 py-1.5 text-[10px] text-text-faint">
          상위 20개만 표시합니다. 전체 {rows.length.toLocaleString()}개
        </p>
      ) : null}
    </div>
  )
}

function FieldValue({
  fieldKey,
  value,
  onSelectEntity,
}: {
  fieldKey: string
  value: unknown
  onSelectEntity?: EntityDetailContentProps['onSelectEntity']
}) {
  if (Array.isArray(value)) {
    const rows = value.filter(
      (item): item is Record<string, unknown> => typeof item === 'object' && item !== null,
    )
    return <RecordTable rows={rows} onSelectEntity={onSelectEntity} />
  }
  if (typeof value === 'object' && value !== null) {
    const row = value as Record<string, unknown>
    const related = entityFromRow(row)
    const display = Object.entries(row)
      .filter(([key]) => !key.endsWith('Id'))
      .map(([key, item]) => formatDashboardValue(key, item))
      .join(' · ')
    return related && onSelectEntity ? (
      <button
        type="button"
        className="text-left font-medium text-info hover:underline"
        onClick={(event) => onSelectEntity(related, event.currentTarget)}
      >
        {display || String(related.id)}
      </button>
    ) : (
      <span>{display}</span>
    )
  }
  return <span>{formatDashboardValue(fieldKey, value)}</span>
}

export function EntityDetailContent({
  detail,
  loading,
  error,
  fallbackProperties,
  onAsk,
  onSelectEntity,
}: EntityDetailContentProps) {
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      {detail ? (
        <>
          <div className="border-b border-border px-5 py-4">
            <p className="text-[10.5px] font-medium tracking-wide text-text-faint uppercase">
              {detail.entity.type}
            </p>
            <h2 className="mt-1 text-[18px] font-bold leading-snug text-text">
              {detail.entity.label}
            </h2>
            <p className="mt-1 text-[11px] text-text-muted">ID {String(detail.entity.id)}</p>
          </div>
          <div className="flex-1 overflow-y-auto">
            {detail.groups.map((group) => (
              <section
                key={group.title}
                className="border-b border-border px-5 py-4 last:border-b-0"
              >
                <h3 className="mb-3 text-[12px] font-semibold text-text">{group.title}</h3>
                <dl className="flex flex-col gap-2.5">
                  {group.fields.map((field) => {
                    const complex = Array.isArray(field.value)
                    return (
                      <div
                        key={field.key}
                        className={
                          complex
                            ? 'flex flex-col gap-2'
                            : 'grid grid-cols-[108px_minmax(0,1fr)] gap-3'
                        }
                      >
                        <dt className="text-[11px] text-text-muted">{field.label}</dt>
                        <dd className="min-w-0 text-[11.5px] font-medium text-text">
                          <FieldValue
                            fieldKey={field.key}
                            value={field.value}
                            onSelectEntity={onSelectEntity}
                          />
                        </dd>
                      </div>
                    )
                  })}
                </dl>
              </section>
            ))}
          </div>
          {detail.actions[0] ? (
            <div className="border-t border-border bg-panel px-4 py-3">
              <Button
                type="button"
                className="w-full"
                onClick={() => onAsk(detail.actions[0].question)}
              >
                <MessageSquareText />
                {detail.actions[0].label}
              </Button>
              <p className="mt-2 line-clamp-2 text-[10px] text-text-faint">
                {detail.actions[0].question}
              </p>
            </div>
          ) : null}
        </>
      ) : loading ? (
        <div className="flex flex-1 flex-col overflow-y-auto p-5">
          <div className="mb-4 flex items-center gap-2 text-[12px] text-text-muted">
            <Loader2 className="size-4 animate-spin" /> 상세 정보를 불러오는 중입니다…
          </div>
          {fallbackProperties && Object.keys(fallbackProperties).length > 0 ? (
            <section>
              <h3 className="mb-2 text-[12px] font-semibold text-text">그래프 속성</h3>
              <dl className="rounded-md border border-border">
                {Object.entries(fallbackProperties).map(([key, value]) => (
                  <div
                    key={key}
                    className="grid grid-cols-[120px_minmax(0,1fr)] gap-2 border-b border-border px-3 py-2 text-[11px] last:border-b-0"
                  >
                    <dt className="truncate text-text-muted">{key}</dt>
                    <dd className="break-words text-text">{formatDashboardValue(key, value)}</dd>
                  </div>
                ))}
              </dl>
            </section>
          ) : null}
        </div>
      ) : (
        <div className="flex flex-1 flex-col gap-4 overflow-y-auto p-5">
          <div
            role="alert"
            className="rounded-md border border-fail/30 bg-fail/5 px-3 py-2.5 text-[11.5px] text-fail"
          >
            {error ?? '상세 정보를 표시할 수 없습니다.'}
          </div>
          {fallbackProperties && Object.keys(fallbackProperties).length > 0 ? (
            <section>
              <h3 className="mb-2 text-[12px] font-semibold text-text">그래프 속성</h3>
              <dl className="rounded-md border border-border">
                {Object.entries(fallbackProperties).map(([key, value]) => (
                  <div
                    key={key}
                    className="grid grid-cols-[120px_minmax(0,1fr)] gap-2 border-b border-border px-3 py-2 text-[11px] last:border-b-0"
                  >
                    <dt className="truncate text-text-muted">{key}</dt>
                    <dd className="break-words text-text">{formatDashboardValue(key, value)}</dd>
                  </div>
                ))}
              </dl>
            </section>
          ) : null}
        </div>
      )}
    </div>
  )
}
