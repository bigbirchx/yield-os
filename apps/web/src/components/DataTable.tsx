"use client";
/**
 * High-performance sortable table for data-dense dashboard views.
 *
 * Built on TanStack Table. Provides:
 * - Configurable columns with type-aware formatting
 * - Click-to-sort with visual indicator
 * - Compact row height for data density
 * - Sticky header on scroll
 * - Row click handler for drill-down
 * - Color-coded APY / USD / change values
 */
import {
  type ColumnDef,
  type SortingState,
  flexRender,
  getCoreRowModel,
  getSortedRowModel,
  useReactTable,
} from "@tanstack/react-table";
import { useState, type CSSProperties, type ReactNode } from "react";

// ---------------------------------------------------------------------------
// Column definition helper type
// ---------------------------------------------------------------------------

export interface DataColumn<T> {
  /** Header label */
  header: string;
  /** Key or accessor function */
  accessorKey?: keyof T & string;
  accessorFn?: (row: T) => unknown;
  /** Custom cell renderer */
  cell?: (value: unknown, row: T) => ReactNode;
  /** Column width (CSS value) */
  width?: string;
  /** Minimum width (CSS value) */
  minWidth?: string;
  /** Enable sorting (default true) */
  sortable?: boolean;
  /** Text alignment (default "left", numbers should be "right") */
  align?: "left" | "center" | "right";
  /** Whether this column contains monospace data */
  mono?: boolean;
}

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

interface DataTableProps<T> {
  columns: DataColumn<T>[];
  data: T[];
  /** Called when a row is clicked. Receives the row data. */
  onRowClick?: (row: T) => void;
  /** Optional className for the wrapper */
  className?: string;
  /** Show a "no data" message when data is empty */
  emptyMessage?: string;
  /** Default sort column key */
  defaultSortKey?: string;
  /** Default sort direction */
  defaultSortDesc?: boolean;
  /** Fixed table layout (default true for data-dense) */
  fixedLayout?: boolean;
  /** Compact mode — tighter rows (default true) */
  compact?: boolean;
  /** Optional class for individual rows. Called with row data. */
  getRowClassName?: (row: T) => string;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export default function DataTable<T>({
  columns,
  data,
  onRowClick,
  className,
  emptyMessage = "No data available",
  defaultSortKey,
  defaultSortDesc = true,
  fixedLayout = true,
  compact = true,
  getRowClassName,
}: DataTableProps<T>) {
  const [sorting, setSorting] = useState<SortingState>(
    defaultSortKey ? [{ id: defaultSortKey, desc: defaultSortDesc }] : []
  );

  // Map our DataColumn defs to TanStack ColumnDefs
  const tanstackCols: ColumnDef<T, unknown>[] = columns.map((col) => {
    const id = col.accessorKey ?? col.header.toLowerCase().replace(/\s+/g, "_");
    return {
      id,
      accessorKey: col.accessorKey,
      accessorFn: col.accessorFn as ((row: T) => unknown) | undefined,
      header: col.header,
      enableSorting: col.sortable !== false,
      cell: (info) => {
        if (col.cell) return col.cell(info.getValue(), info.row.original);
        const val = info.getValue();
        if (val == null) return <span className="dt-null">--</span>;
        return String(val);
      },
      meta: { align: col.align, width: col.width, minWidth: col.minWidth, mono: col.mono },
    };
  });

  const table = useReactTable({
    data,
    columns: tanstackCols,
    state: { sorting },
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
  });

  return (
    <div className={`dt-wrap ${className ?? ""}`}>
      <table
        className="dt-table"
        style={{ tableLayout: fixedLayout ? "fixed" : "auto" }}
      >
        <thead className="dt-thead">
          {table.getHeaderGroups().map((hg) => (
            <tr key={hg.id}>
              {hg.headers.map((header) => {
                const meta = header.column.columnDef.meta as {
                  align?: string;
                  width?: string;
                  minWidth?: string;
                  mono?: boolean;
                } | undefined;
                const style: CSSProperties = {
                  width: meta?.width,
                  minWidth: meta?.minWidth,
                  textAlign: (meta?.align as CSSProperties["textAlign"]) ?? "left",
                };
                const canSort = header.column.getCanSort();
                const sorted = header.column.getIsSorted();
                return (
                  <th
                    key={header.id}
                    className={`dt-th ${canSort ? "dt-sortable" : ""} ${sorted ? "dt-sorted" : ""}`}
                    style={style}
                    onClick={canSort ? header.column.getToggleSortingHandler() : undefined}
                  >
                    {flexRender(header.column.columnDef.header, header.getContext())}
                    {sorted === "asc" && <span className="dt-sort-icon"> &#9650;</span>}
                    {sorted === "desc" && <span className="dt-sort-icon"> &#9660;</span>}
                  </th>
                );
              })}
            </tr>
          ))}
        </thead>
        <tbody className="dt-tbody">
          {table.getRowModel().rows.length === 0 ? (
            <tr>
              <td colSpan={columns.length} className="dt-empty">
                {emptyMessage}
              </td>
            </tr>
          ) : (
            table.getRowModel().rows.map((row) => (
              <tr
                key={row.id}
                className={`dt-row ${compact ? "dt-compact" : ""} ${onRowClick ? "dt-clickable" : ""} ${getRowClassName ? getRowClassName(row.original) : ""}`}
                onClick={onRowClick ? () => onRowClick(row.original) : undefined}
              >
                {row.getVisibleCells().map((cell) => {
                  const meta = cell.column.columnDef.meta as {
                    align?: string;
                    width?: string;
                    mono?: boolean;
                  } | undefined;
                  const style: CSSProperties = {
                    textAlign: (meta?.align as CSSProperties["textAlign"]) ?? "left",
                  };
                  return (
                    <td
                      key={cell.id}
                      className={`dt-td ${meta?.mono ? "dt-mono" : ""}`}
                      style={style}
                    >
                      {flexRender(cell.column.columnDef.cell, cell.getContext())}
                    </td>
                  );
                })}
              </tr>
            ))
          )}
        </tbody>
      </table>
    </div>
  );
}
