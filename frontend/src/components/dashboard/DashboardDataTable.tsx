import type { KeyboardEvent } from 'react'
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
}: DashboardDataTableProps) {
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
      <table className="w-full border-collapse text-[11.5px]">
        <thead>
          <tr className="border-b border-border bg-panel-2/65 text-text-muted">
            <th className="w-10 px-3 py-2 text-right font-medium">순위</th>
            {columns.map((column) => (
              <th
                key={column}
                className={`px-3 py-2 font-medium whitespace-nowrap ${isNumericColumn(column) ? 'text-right' : 'text-left'}`}
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
                <td className="px-3 py-2 text-right text-text-faint">{index + 1}</td>
                {columns.map((column) => (
                  <td
                    key={column}
                    className={`max-w-60 px-3 ${compact ? 'py-2' : 'py-2.5'} whitespace-nowrap ${
                      isNumericColumn(column) ? 'text-right tabular-nums' : 'truncate text-left'
                    } ${column === 'shortageQty' || column === 'scrappedQty' ? 'font-semibold text-fail' : ''}`}
                    title={formatDashboardValue(column, row[column])}
                  >
                    {formatDashboardValue(column, row[column])}
                  </td>
                ))}
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
        <div className="flex min-h-28 items-center justify-center text-[12px] text-text-faint">
          표시할 결과가 없습니다.
        </div>
      ) : null}
    </div>
  )
}
