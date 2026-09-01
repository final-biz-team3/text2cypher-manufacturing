import type { KeyboardEvent } from 'react'
import { Inbox } from 'lucide-react'
import { COLUMN_LABELS, formatDashboardValue } from './dashboardFormatters'

interface DashboardDataTableProps {
  columns: string[]
  rows: Record<string, unknown>[]
  selectedId?: string | number | null
  entityIdField?: string
  onRowSelect?: (row: Record<string, unknown>, trigger: HTMLElement) => void
  onSort?: (column: string) => void
  sortableColumns?: string[]
  sort?: string
  direction?: 'asc' | 'desc'
  compact?: boolean
  barColumn?: string
  barTone?: 'info' | 'warn' | 'fail'
  responsiveHiddenColumns?: string[]
}

function isNumericColumn(column: string) {
  return /(?:Id|Qty|Count|Stock|Price|sequence|bin)$/.test(column)
}

export function DashboardDataTable({
  columns,
  rows,
  selectedId,
  entityIdField,
  onRowSelect,
  onSort,
  sortableColumns,
  sort,
  direction,
  compact = false,
  barColumn,
  barTone = 'info',
  responsiveHiddenColumns = [],
}: DashboardDataTableProps) {
  const barMaximum = barColumn
    ? Math.max(0, ...rows.map((row) => (typeof row[barColumn] === 'number' ? row[barColumn] : 0)))
    : 0
  const activateRow = (
    row: Record<string, unknown>,
    target: EventTarget | null,
    event?: KeyboardEvent<HTMLTableRowElement>,
  ) => {
    if (event && event.key !== 'Enter' && event.key !== ' ') return
    event?.preventDefault()
    if (target instanceof HTMLElement) onRowSelect?.(row, target)
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse text-[12.5px]">
        <thead>
          <tr className="border-b border-border bg-panel-2/65 text-text-muted">
            <th className="h-10 w-10 px-3 text-right text-[11.5px] font-semibold">순위</th>
            {columns.map((column) => (
              <th
                key={column}
                className={`h-10 px-3 text-[11.5px] font-semibold whitespace-nowrap ${responsiveHiddenColumns.includes(column) ? 'hidden 2xl:table-cell' : ''} ${isNumericColumn(column) ? 'text-right' : 'text-left'}`}
              >
                {onSort && sortableColumns?.includes(column) ? (
                  <button
                    type="button"
                    className="inline-flex items-center gap-1 rounded-sm hover:text-info focus-visible:ring-2 focus-visible:ring-ring/30"
                    onClick={() => onSort(column)}
                  >
                    {COLUMN_LABELS[column] ?? column}
                    {sort === column ? (
                      <span aria-hidden="true">{direction === 'asc' ? '↑' : '↓'}</span>
                    ) : null}
                  </button>
                ) : (
                  (COLUMN_LABELS[column] ?? column)
                )}
              </th>
            ))}
            {selectedId !== undefined ? <th className="sr-only">선택 상태</th> : null}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => {
            const rowId = entityIdField ? row[entityIdField] : undefined
            const isSelected = rowId === selectedId
            return (
              <tr
                key={`${String(rowId ?? index)}-${index}`}
                tabIndex={onRowSelect ? 0 : undefined}
                role={onRowSelect ? 'button' : undefined}
                aria-selected={isSelected}
                data-state={isSelected ? 'selected' : undefined}
                onClick={(event) => activateRow(row, event.currentTarget)}
                onKeyDown={(event) => activateRow(row, event.currentTarget, event)}
                className={`border-b border-border/80 outline-none transition-colors last:border-b-0 ${
                  onRowSelect
                    ? 'cursor-pointer hover:bg-accent-bg/60 focus-visible:bg-accent-bg focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring/45'
                    : ''
                } ${isSelected ? 'bg-accent-bg text-info' : 'text-text'}`}
              >
                <td className="h-11 px-3 text-right text-text-faint">{index + 1}</td>
                {columns.map((column) => {
                  const value = row[column]
                  const showBar =
                    column === barColumn && typeof value === 'number' && barMaximum > 0
                  return (
                    <td
                      key={column}
                      className={`h-11 max-w-60 px-3 ${responsiveHiddenColumns.includes(column) ? 'hidden 2xl:table-cell' : ''} ${compact ? '' : 'py-0.5'} whitespace-nowrap ${
                        isNumericColumn(column) ? 'text-right tabular-nums' : 'truncate text-left'
                      } ${column === 'shortageQty' || column === 'scrappedQty' ? 'font-semibold text-fail' : ''}`}
                      title={formatDashboardValue(column, value)}
                    >
                      {showBar ? (
                        <span className="flex min-w-24 items-center justify-end gap-2.5">
                          <span className="min-w-10">{formatDashboardValue(column, value)}</span>
                          <span
                            className="h-1.5 w-14 overflow-hidden bg-panel-2 sm:w-16"
                            aria-hidden="true"
                          >
                            <span
                              className={`block h-full ${
                                barTone === 'warn'
                                  ? 'bg-warn'
                                  : barTone === 'fail'
                                    ? 'bg-fail'
                                    : 'bg-info'
                              }`}
                              style={{ width: `${Math.max(8, (value / barMaximum) * 100)}%` }}
                            />
                          </span>
                        </span>
                      ) : (
                        formatDashboardValue(column, value)
                      )}
                    </td>
                  )
                })}
                {selectedId !== undefined ? (
                  <td className="px-2 text-[10px] font-semibold whitespace-nowrap">
                    {isSelected ? '선택됨' : ''}
                  </td>
                ) : null}
              </tr>
            )
          })}
        </tbody>
      </table>
      {rows.length === 0 ? (
        <div className="flex min-h-32 flex-col items-center justify-center gap-2 text-[12px] text-text-faint">
          <span className="flex size-8 items-center justify-center rounded-full bg-panel-2">
            <Inbox className="size-4" aria-hidden="true" />
          </span>
          <span>표시할 결과가 없습니다.</span>
        </div>
      ) : null}
    </div>
  )
}
