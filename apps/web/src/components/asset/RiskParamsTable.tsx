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
import type { ProtocolRiskParams } from "@/types/api";

const col = createColumnHelper<ProtocolRiskParams>();

function pct(v: number | null) {
  return v != null ? `${(v * 100).toFixed(1)}%` : "—";
}
function flag(v: boolean | null) {
  if (v == null) return <span className="cell-dim">—</span>;
  return v ? <span className="cell-green">✓</span> : <span className="cell-red">✗</span>;
}

const COLUMNS = [
  col.accessor("protocol", {
    header: "Protocol",
    cell: (i) => <span className="cell-bold">{i.getValue()}</span>,
  }),
  col.accessor("chain", {
    header: "Chain",
    cell: (i) => <span className="cell-dim">{i.getValue()}</span>,
  }),
  col.accessor("debt_asset", {
    header: "Debt",
    cell: (i) => <span className="cell-dim">{i.getValue() ?? "—"}</span>,
  }),
  col.accessor("max_ltv", {
    header: "Max LTV",
    cell: (i) => <span className="cell-yellow">{pct(i.getValue())}</span>,
  }),
  col.accessor("liquidation_threshold", {
    header: "Liq. Thresh.",
    cell: (i) => <span className="cell-orange">{pct(i.getValue())}</span>,
  }),
  col.accessor("liquidation_penalty", {
    header: "Liq. Penalty",
    cell: (i) => <span className="cell-mono">{pct(i.getValue())}</span>,
  }),
  col.accessor("collateral_eligible", {
    header: "Collateral",
    cell: (i) => flag(i.getValue()),
  }),
  col.accessor("borrowing_enabled", {
    header: "Borrowable",
    cell: (i) => flag(i.getValue()),
  }),
  col.accessor("is_active", {
    header: "Active",
    cell: (i) => flag(i.getValue()),
  }),
  col.accessor("snapshot_at", {
    header: "Fresh",
    cell: (i) => <FreshnessTag isoTimestamp={i.getValue()} />,
  }),
];

export function RiskParamsTable({ rows }: { rows: ProtocolRiskParams[] }) {
  const [sorting, setSorting] = useState<SortingState>([
    { id: "max_ltv", desc: true },
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
