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

/**
 * Returns a human-readable market context string for a row.
 *
 * morpho_blue — isolated pair, market = "COLLATERAL/LOAN"
 *               → display as "COLLATERAL → LOAN" (loan asset = symbol)
 * kamino      — pool model, market = market name ("Main Market", "JLP Market")
 *               → display market name directly
 * aave / aave-v3 — pool model, market = deployment name
 *               → display chain
 * others      — display market name or "—"
 */
function marketContext(row: LendingMarket): string {
  const { protocol, market, chain } = row;
  if (protocol === "morpho_blue") {
    const slash = market.indexOf("/");
    if (slash > 0) {
      const collateral = market.slice(0, slash);
      const loan = market.slice(slash + 1);
      return collateral === loan ? market : `${collateral} → ${loan}`;
    }
    return market;
  }
  if (protocol === "kamino") return market;
  if (protocol === "aave" || protocol === "aave-v3") return chain ?? "Ethereum";
  return market || "—";
}

/** Short human-readable protocol badge. */
function protocolLabel(protocol: string): string {
  if (protocol === "morpho_blue") return "Morpho";
  if (protocol === "kamino") return "Kamino";
  if (protocol === "aave" || protocol === "aave-v3") return "Aave";
  return protocol;
}

const COLUMNS = [
  col.accessor("protocol", {
    header: "Protocol",
    cell: (i) => (
      <div>
        <span className="cell-bold">{protocolLabel(i.getValue())}</span>
        <div className="cell-dim" style={{ fontSize: "0.75em", marginTop: 1 }}>
          {marketContext(i.row.original)}
        </div>
      </div>
    ),
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
