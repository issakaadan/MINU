import { useMemo, useState, type ReactNode } from "react";

import { Panel } from "./Panel";

type Column<T> = {
  header: string;
  cell: (row: T) => ReactNode;
};

type DataTableProps<T> = {
  title: string;
  subtitle?: string;
  rows: T[];
  columns: Column<T>[];
  searchPlaceholder: string;
  searchAccessor?: (row: T) => string;
  actions?: ReactNode;
  toolbarExtras?: ReactNode;
  emptyMessage?: string;
  loading?: boolean;
  rowKey?: (row: T, rowIndex: number) => string | number;
};

export function DataTable<T>({
  title,
  subtitle,
  rows,
  columns,
  searchPlaceholder,
  searchAccessor,
  actions,
  toolbarExtras,
  emptyMessage = "No records yet.",
  loading = false,
  rowKey,
}: DataTableProps<T>) {
  const [query, setQuery] = useState("");

  const filteredRows = useMemo(() => {
    if (!query || !searchAccessor) {
      return rows;
    }

    const normalizedQuery = query.toLowerCase();
    return rows.filter((row) =>
      searchAccessor(row).toLowerCase().includes(normalizedQuery),
    );
  }, [query, rows, searchAccessor]);

  return (
    <Panel
      title={title}
      subtitle={subtitle}
      actions={
        <div className="table-toolbar">
          <input
            className="input"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder={searchPlaceholder}
          />
          {toolbarExtras}
          {actions}
        </div>
      }
    >
      <div className="table-wrap">
        <table className="table">
          <thead>
            <tr>
              {columns.map((column) => (
                <th key={column.header}>{column.header}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr>
                <td colSpan={columns.length} className="table-empty">
                  <div className="loading-state loading-state--compact">
                    <span className="loading-state__spinner" aria-hidden="true" />
                    <div>
                      <strong>Loading</strong>
                      <div>Refreshing table data from the local API.</div>
                    </div>
                  </div>
                </td>
              </tr>
            ) : filteredRows.length > 0 ? (
              filteredRows.map((row, rowIndex) => (
                <tr key={rowKey ? rowKey(row, rowIndex) : rowIndex}>
                  {columns.map((column) => (
                    <td key={column.header}>{column.cell(row)}</td>
                  ))}
                </tr>
              ))
            ) : (
              <tr>
                <td colSpan={columns.length} className="table-empty">
                  {emptyMessage}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </Panel>
  );
}
