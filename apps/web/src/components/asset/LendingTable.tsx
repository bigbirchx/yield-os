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
import type { LendingMarket } from "@/types/api";

const col = createColumnHelper<LendingMarket>();

function pct(v: number | null) {
  return v != null ? `${v.toFixed(2)}%` : "—";
}
function usd(v: number | null) {
  if (v == null) return "—";
  if (v >= 1e9) return `$${(v / 1e9).toFixed(1)}B`;
  if (v >= 1e6) return `$${(v / 1e6).toFixed(0)}M`;
  return `$${v.toFixed(0)}`;
}

const COLUMNS = [
  col.accessor("protocol", {
    header: "Protocol",
    cell: (i) => <span className="cell-bold">{i.getValue()}</span>,
  }),
  col.accessor("chain", {
    header: "Chain",
    cell: (i) => <span className="cell-dim">{i.getValue() ?? "—"}</span>,
  }),
  col.accessor("supply_apy", {
    header: "Supply APY",
    cell: (i) => <span className="cell-green">{pct(i.getValue())}</span>,
  }),
  col.accessor("reward_supply_apy", {
    header: "+Reward",
    cell: (i) => {
      const v = i.getValue();
      return v ? <span className="cell-accent">+{pct(v)}</span> : <span className="cell-dim">—</span>;
    },
  }),
  col.accessor("borrow_apy", {
    header: "Borrow APY",
    cell: (i) => <span className="cell-red">{pct(i.getValue())}</span>,
  }),
  col.accessor("utilization", {
    header: "Util",
    cell: (i) => {
      const v = i.getValue();
      const cls = v != null && v > 0.9 ? "cell-red" : "cell-yellow";
      return <span className={cls}>{v != null ? pct(v * 100) : "—"}</span>;
    },
  }),
  col.accessor("tvl_usd", {
    header: "TVL",
    cell: (i) => <span className="cell-mono">{usd(i.getValue())}</span>,
  }),
  col.accessor("available_liquidity_usd", {
    header: "Available",
    cell: (i) => <span className="cell-mono">{usd(i.getValue())}</span>,
  }),
  col.accessor("snapshot_at", {
    header: "Fresh",
    cell: (i) => <FreshnessTag isoTimestamp={i.getValue()} />,
  }),
];

export function LendingTable({ rows }: { rows: LendingMarket[] }) {
  const [sorting, setSorting] = useState<SortingState>([
    { id: "borrow_apy", desc: true },
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
