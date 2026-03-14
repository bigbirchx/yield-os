"use client";

import {
  createColumnHelper,
  flexRender,
  getCoreRowModel,
  getSortedRowModel,
  useReactTable,
  type SortingState,
} from "@tanstack/react-table";
import { useState } from "react";

import { FreshnessTag } from "@/components/overview/FreshnessTag";
import type { DerivativesSnapshot } from "@/types/api";

const col = createColumnHelper<DerivativesSnapshot>();

function pct(v: number | null, d = 4) {
  return v != null ? `${(v * 100).toFixed(d)}%` : "—";
}
function usd(v: number | null) {
  if (v == null) return "—";
  if (v >= 1e9) return `$${(v / 1e9).toFixed(1)}B`;
  if (v >= 1e6) return `$${(v / 1e6).toFixed(0)}M`;
  return `$${v.toFixed(0)}`;
}
function price(v: number | null) {
  return v != null ? `$${v.toLocaleString("en-US", { maximumFractionDigits: 2 })}` : "—";
}

const COLUMNS = [
  col.accessor("venue", {
    header: "Venue",
    cell: (i) => <span className="cell-bold">{i.getValue()}</span>,
  }),
  col.accessor("funding_rate", {
    header: "Funding / 8h",
    cell: (i) => {
      const v = i.getValue();
      const cls = v != null && v < 0 ? "cell-red" : "cell-yellow";
      return <span className={cls}>{pct(v)}</span>;
    },
  }),
  col.accessor("open_interest_usd", {
    header: "Open Interest",
    cell: (i) => <span className="cell-mono">{usd(i.getValue())}</span>,
  }),
  col.accessor("basis_annualized", {
    header: "Basis (ann.)",
    cell: (i) => {
      const v = i.getValue();
      return <span className="cell-yellow">{v != null ? pct(v, 2) : "—"}</span>;
    },
  }),
  col.accessor("mark_price", {
    header: "Mark",
    cell: (i) => <span className="cell-mono">{price(i.getValue())}</span>,
  }),
  col.accessor("index_price", {
    header: "Index",
    cell: (i) => <span className="cell-mono">{price(i.getValue())}</span>,
  }),
  col.accessor("perp_volume_usd", {
    header: "Perp Vol",
    cell: (i) => <span className="cell-mono">{usd(i.getValue())}</span>,
  }),
  col.accessor("snapshot_at", {
    header: "Fresh",
    cell: (i) => <FreshnessTag isoTimestamp={i.getValue()} />,
  }),
];

export function DerivativesTable({ rows }: { rows: DerivativesSnapshot[] }) {
  const [sorting, setSorting] = useState<SortingState>([
    { id: "open_interest_usd", desc: true },
  ]);

  const table = useReactTable({
    data: rows,
    columns: COLUMNS,
    state: { sorting },
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
  });

  return (
    <div className="data-table-wrap">
      <table className="data-table">
        <thead>
          {table.getHeaderGroups().map((hg) => (
            <tr key={hg.id}>
              {hg.headers.map((h) => (
                <th
                  key={h.id}
                  onClick={h.column.getToggleSortingHandler()}
                  className={h.column.getCanSort() ? "sortable" : ""}
                >
                  {flexRender(h.column.columnDef.header, h.getContext())}
                  {h.column.getIsSorted() === "asc" ? " ↑" : h.column.getIsSorted() === "desc" ? " ↓" : ""}
                </th>
              ))}
            </tr>
          ))}
        </thead>
        <tbody>
          {table.getRowModel().rows.map((row) => (
            <tr key={row.id}>
              {row.getVisibleCells().map((cell) => (
                <td key={cell.id}>
                  {flexRender(cell.column.columnDef.cell, cell.getContext())}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
