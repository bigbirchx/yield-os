"use client";

/**
 * Dated Futures Basis Dashboard
 *
 * Section 1 — Basis Term Structure Chart  (DTE on X, ann basis % on Y, per venue)
 * Section 2 — Basis Snapshot Table        (sortable, color-coded ann basis %)
 * Section 3 — Historical Basis Chart      (click row → load history for that contract)
 * Section 4 — Controls                    (symbol, venues, USD vs % toggle)
 */

import dynamic from "next/dynamic";
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  fetchBasisHistory,
  fetchBasisSnapshot,
  type BasisHistory,
  type BasisSnapshot,
  type BasisTermRow,
} from "@/lib/api";

const ReactECharts = dynamic(() => import("echarts-for-react"), { ssr: false });

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const SYMBOLS = ["BTC", "ETH"];
const ALL_VENUES = ["deribit", "binance", "okx", "bybit", "cme"] as const;
type Venue = (typeof ALL_VENUES)[number];

const VENUE_COLORS: Record<Venue, string> = {
  deribit:  "#6366f1",
  binance:  "#f59e0b",
  okx:      "#22c55e",
  bybit:    "#3b82f6",
  cme:      "#f97316",
};

const VENUE_LABELS: Record<Venue, string> = {
  deribit: "Deribit",
  binance: "Binance",
  okx:     "OKX",
  bybit:   "Bybit",
  cme:     "CME",
};

function fmtPct(v: number | null | undefined, decimals = 2): string {
  if (v == null) return "—";
  return `${(v * 100).toFixed(decimals)}%`;
}

function fmtUsd(v: number | null | undefined): string {
  if (v == null) return "—";
  if (Math.abs(v) >= 1e9) return `$${(v / 1e9).toFixed(2)}B`;
  if (Math.abs(v) >= 1e6) return `$${(v / 1e6).toFixed(1)}M`;
  if (Math.abs(v) >= 1e3) return `$${(v / 1e3).toFixed(1)}K`;
  return `$${v.toFixed(2)}`;
}

function fmtPrice(v: number | null | undefined): string {
  if (v == null) return "—";
  return `$${v.toLocaleString(undefined, { maximumFractionDigits: 2 })}`;
}

function basisColor(pct: number | null | undefined): string {
  if (pct == null) return "var(--text-secondary)";
  if (pct > 0.12) return "#ef4444";
  if (pct > 0.06) return "#f97316";
  if (pct > 0.02) return "#f59e0b";
  if (pct >= 0)   return "#22c55e";
  return "#6366f1";
}

// ---------------------------------------------------------------------------
// Term Structure ECharts option
// ---------------------------------------------------------------------------

function buildTermStructureOption(
  rows: BasisTermRow[],
  activeVenues: Set<string>,
  showUsd: boolean
) {
  const seriesByVenue: Record<string, [number, number, string, string][]> = {};
  for (const r of rows) {
    if (!activeVenues.has(r.venue)) continue;
    if (!seriesByVenue[r.venue]) seriesByVenue[r.venue] = [];
    const yVal = showUsd ? r.basis_usd : (r.basis_pct_ann ?? 0) * 100;
    seriesByVenue[r.venue].push([
      r.days_to_expiry,
      yVal,
      r.contract,
      r.expiry,
    ]);
  }

  const series = Object.entries(seriesByVenue).map(([venue, data]) => ({
    name: VENUE_LABELS[venue as Venue] ?? venue,
    type: "scatter",
    symbolSize: 12,
    itemStyle: { color: VENUE_COLORS[venue as Venue] ?? "#888" },
    data: data.map(([dte, y, contract, expiry]) => ({
      value: [dte, y],
      contract,
      expiry,
    })),
  }));

  return {
    backgroundColor: "transparent",
    tooltip: {
      trigger: "item",
      backgroundColor: "#1e2328",
      borderColor: "#2a3040",
      textStyle: { color: "#e8eaed", fontSize: 12 },
      formatter: (params: any) => {
        const d = params.data;
        const basisUsdRow = rows.find((r) => r.contract === d.contract);
        return [
          `<strong>${d.contract}</strong>`,
          `Expiry: ${d.expiry ? new Date(d.expiry).toLocaleDateString() : "—"}`,
          showUsd
            ? `Basis USD: ${fmtUsd(d.value[1])}`
            : `Basis Ann: ${fmtPct(d.value[1] / 100)}`,
          basisUsdRow ? `OI: ${fmtUsd(basisUsdRow.oi_usd)}` : "",
          basisUsdRow ? `24h Vol: ${fmtUsd(basisUsdRow.volume_24h_usd)}` : "",
        ]
          .filter(Boolean)
          .join("<br/>");
      },
    },
    legend: {
      top: 8,
      right: 16,
      textStyle: { color: "#8b9099", fontSize: 11 },
    },
    grid: { left: 60, right: 20, top: 48, bottom: 40 },
    xAxis: {
      name: "Days to Expiry",
      nameTextStyle: { color: "#8b9099", fontSize: 11 },
      nameLocation: "end",
      axisLine: { lineStyle: { color: "#1e2328" } },
      axisLabel: { color: "#8b9099", fontSize: 11 },
      splitLine: { lineStyle: { color: "#1e2328" } },
    },
    yAxis: {
      name: showUsd ? "Basis USD" : "Ann Basis %",
      nameTextStyle: { color: "#8b9099", fontSize: 11 },
      axisLine: { lineStyle: { color: "#1e2328" } },
      axisLabel: {
        color: "#8b9099",
        fontSize: 11,
        formatter: showUsd
          ? (v: number) => fmtUsd(v)
          : (v: number) => `${v.toFixed(1)}%`,
      },
      splitLine: { lineStyle: { color: "#1e2328" } },
    },
    series,
  };
}

// ---------------------------------------------------------------------------
// History ECharts option
// ---------------------------------------------------------------------------

function buildHistoryOption(history: BasisHistory, showUsd: boolean) {
  const data = history.series
    .filter((p) => (showUsd ? p.basis_usd != null : p.basis_pct_ann != null))
    .map((p) => [
      p.timestamp,
      showUsd ? p.basis_usd : (p.basis_pct_ann ?? 0) * 100,
    ]);

  return {
    backgroundColor: "transparent",
    tooltip: {
      trigger: "axis",
      backgroundColor: "#1e2328",
      borderColor: "#2a3040",
      textStyle: { color: "#e8eaed", fontSize: 12 },
      formatter: (params: any) => {
        const p = params[0];
        return [
          `<strong>${new Date(p.value[0]).toLocaleDateString()}</strong>`,
          showUsd
            ? `Basis: ${fmtUsd(p.value[1])}`
            : `Basis Ann: ${fmtPct(p.value[1] / 100)}`,
        ].join("<br/>");
      },
    },
    grid: { left: 60, right: 20, top: 24, bottom: 40 },
    xAxis: {
      type: "time",
      axisLine: { lineStyle: { color: "#1e2328" } },
      axisLabel: { color: "#8b9099", fontSize: 11 },
      splitLine: { lineStyle: { color: "#1e2328" } },
    },
    yAxis: {
      name: showUsd ? "Basis USD" : "Ann Basis %",
      nameTextStyle: { color: "#8b9099", fontSize: 11 },
      axisLine: { lineStyle: { color: "#1e2328" } },
      axisLabel: {
        color: "#8b9099",
        fontSize: 11,
        formatter: showUsd
          ? (v: number) => fmtUsd(v)
          : (v: number) => `${v.toFixed(1)}%`,
      },
      splitLine: { lineStyle: { color: "#1e2328" } },
    },
    series: [
      {
        type: "line",
        data,
        showSymbol: false,
        lineStyle: { color: "#3b82f6", width: 1.5 },
        areaStyle: { color: "rgba(59,130,246,0.08)" },
      },
    ],
  };
}

// ---------------------------------------------------------------------------
// Main page component
// ---------------------------------------------------------------------------

export default function BasisPage() {
  const [symbol, setSymbol]           = useState("BTC");
  const [activeVenues, setActiveVenues] = useState<Set<Venue>>(new Set(ALL_VENUES));
  const [showUsd, setShowUsd]         = useState(false);
  const [snapshot, setSnapshot]       = useState<BasisSnapshot | null>(null);
  const [history, setHistory]         = useState<BasisHistory | null>(null);
  const [selectedRow, setSelectedRow] = useState<BasisTermRow | null>(null);
  const [loadingSnap, setLoadingSnap] = useState(false);
  const [loadingHist, setLoadingHist] = useState(false);
  const [snapError, setSnapError]     = useState<string | null>(null);
  const [histDays, setHistDays]       = useState(89);

  const API_URL =
    process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

  const loadSnapshot = useCallback(async () => {
    setLoadingSnap(true);
    setSnapError(null);
    try {
      const data = await fetchBasisSnapshot(symbol);
      setSnapshot(data);
    } catch {
      setSnapError("Failed to load snapshot");
    } finally {
      setLoadingSnap(false);
    }
  }, [symbol]);

  useEffect(() => {
    loadSnapshot();
  }, [loadSnapshot]);

  const handleRowClick = useCallback(
    async (row: BasisTermRow) => {
      setSelectedRow(row);
      setHistory(null);
      setLoadingHist(true);
      try {
        const data = await fetchBasisHistory(symbol, row.venue, row.contract, histDays);
        setHistory(data);
      } finally {
        setLoadingHist(false);
      }
    },
    [symbol, histDays]
  );

  const toggleVenue = (venue: Venue) => {
    setActiveVenues((prev) => {
      const next = new Set(prev);
      if (next.has(venue)) {
        next.delete(venue);
      } else {
        next.add(venue);
      }
      return next;
    });
  };

  const filteredRows = useMemo(
    () =>
      (snapshot?.term_structure ?? []).filter((r) =>
        activeVenues.has(r.venue as Venue)
      ),
    [snapshot, activeVenues]
  );

  const termStructureOption = useMemo(
    () =>
      filteredRows.length > 0
        ? buildTermStructureOption(filteredRows, activeVenues, showUsd)
        : null,
    [filteredRows, activeVenues, showUsd]
  );

  const historyOption = useMemo(
    () =>
      history && history.series.length > 0
        ? buildHistoryOption(history, showUsd)
        : null,
    [history, showUsd]
  );

  return (
    <div className="bs-page">
      {/* ------------------------------------------------------------------ */}
      {/* Header + Controls                                                    */}
      {/* ------------------------------------------------------------------ */}
      <div className="bs-header">
        <div className="bs-title-block">
          <h1 className="bs-title">Dated Futures Basis</h1>
          {snapshot && (
            <span className="bs-as-of">
              as of {new Date(snapshot.as_of).toLocaleTimeString()}
            </span>
          )}
        </div>

        <div className="bs-controls">
          {/* Symbol */}
          <div className="bs-control-group">
            <span className="bs-control-label">Symbol</span>
            {SYMBOLS.map((s) => (
              <button
                key={s}
                className={`bs-btn ${symbol === s ? "bs-btn--active" : ""}`}
                onClick={() => {
                  setSymbol(s);
                  setSnapshot(null);
                  setHistory(null);
                  setSelectedRow(null);
                }}
              >
                {s}
              </button>
            ))}
          </div>

          {/* Venues */}
          <div className="bs-control-group">
            <span className="bs-control-label">Venues</span>
            {ALL_VENUES.map((v) => (
              <button
                key={v}
                className={`bs-btn ${activeVenues.has(v) ? "bs-btn--active" : ""}`}
                style={
                  activeVenues.has(v)
                    ? { borderColor: VENUE_COLORS[v], color: VENUE_COLORS[v] }
                    : {}
                }
                onClick={() => toggleVenue(v)}
              >
                {VENUE_LABELS[v]}
              </button>
            ))}
          </div>

          {/* Display toggle */}
          <div className="bs-control-group">
            <span className="bs-control-label">Display</span>
            <button
              className={`bs-btn ${!showUsd ? "bs-btn--active" : ""}`}
              onClick={() => setShowUsd(false)}
            >
              Ann %
            </button>
            <button
              className={`bs-btn ${showUsd ? "bs-btn--active" : ""}`}
              onClick={() => setShowUsd(true)}
            >
              USD Basis
            </button>
          </div>

          <button className="bs-btn bs-btn--refresh" onClick={loadSnapshot}>
            ↺ Refresh
          </button>
        </div>
      </div>

      {loadingSnap && <div className="bs-loading">Loading term structure…</div>}
      {snapError && <div className="bs-error">{snapError}</div>}

      {/* ------------------------------------------------------------------ */}
      {/* Section 1 — Term Structure Chart                                    */}
      {/* ------------------------------------------------------------------ */}
      {termStructureOption && (
        <section className="bs-section bs-section--chart">
          <h2 className="bs-section-title">Term Structure</h2>
          <ReactECharts
            option={termStructureOption}
            style={{ height: 320 }}
            notMerge
          />
        </section>
      )}

      {/* ------------------------------------------------------------------ */}
      {/* Section 2 — Snapshot Table                                          */}
      {/* ------------------------------------------------------------------ */}
      {filteredRows.length > 0 && (
        <section className="bs-section">
          <h2 className="bs-section-title">
            Snapshot Table
            <span className="bs-section-hint">click a row to load history</span>
          </h2>
          <div className="bs-table-wrap">
            <table className="bs-table">
              <thead>
                <tr>
                  <th>Venue</th>
                  <th>Contract</th>
                  <th>Expiry</th>
                  <th>DTE</th>
                  <th>Futures Price</th>
                  <th>Index Price</th>
                  <th>Basis USD</th>
                  <th>Basis Ann %</th>
                  <th>OI (USD)</th>
                  <th>24h Vol (USD)</th>
                </tr>
              </thead>
              <tbody>
                {filteredRows.map((r) => {
                  const isSelected =
                    selectedRow?.contract === r.contract &&
                    selectedRow?.venue === r.venue;
                  return (
                    <tr
                      key={`${r.venue}-${r.contract}`}
                      className={`bs-tr ${isSelected ? "bs-tr--selected" : ""}`}
                      onClick={() => handleRowClick(r)}
                    >
                      <td>
                        <span
                          className="bs-venue-dot"
                          style={{ background: VENUE_COLORS[r.venue as Venue] }}
                        />
                        {VENUE_LABELS[r.venue as Venue] ?? r.venue}
                      </td>
                      <td className="bs-mono">{r.contract}</td>
                      <td className="bs-mono">
                        {new Date(r.expiry).toLocaleDateString()}
                      </td>
                      <td className="bs-mono">{r.days_to_expiry}d</td>
                      <td className="bs-mono">{fmtPrice(r.futures_price)}</td>
                      <td className="bs-mono">{fmtPrice(r.index_price)}</td>
                      <td
                        className="bs-mono"
                        style={{ color: r.basis_usd >= 0 ? "#22c55e" : "#ef4444" }}
                      >
                        {fmtUsd(r.basis_usd)}
                      </td>
                      <td
                        className="bs-mono bs-basis-cell"
                        style={{ color: basisColor(r.basis_pct_ann) }}
                      >
                        {fmtPct(r.basis_pct_ann)}
                      </td>
                      <td className="bs-mono">{fmtUsd(r.oi_usd)}</td>
                      <td className="bs-mono">{fmtUsd(r.volume_24h_usd)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {/* ------------------------------------------------------------------ */}
      {/* Section 3 — Historical Basis Chart                                  */}
      {/* ------------------------------------------------------------------ */}
      {selectedRow && (
        <section className="bs-section">
          <div className="bs-hist-header">
            <h2 className="bs-section-title">
              Historical Basis —{" "}
              <span style={{ color: VENUE_COLORS[selectedRow.venue as Venue] }}>
                {VENUE_LABELS[selectedRow.venue as Venue] ?? selectedRow.venue}
              </span>{" "}
              {selectedRow.contract}
            </h2>
            <div className="bs-hist-days">
              {[14, 30, 89].map((d) => (
                <button
                  key={d}
                  className={`bs-btn ${histDays === d ? "bs-btn--active" : ""}`}
                  onClick={() => {
                    setHistDays(d);
                    handleRowClick(selectedRow);
                  }}
                >
                  {d}d
                </button>
              ))}
            </div>
          </div>

          {loadingHist && (
            <div className="bs-loading">Loading history…</div>
          )}

          {historyOption && !loadingHist && (
            <ReactECharts
              option={historyOption}
              style={{ height: 260 }}
              notMerge
            />
          )}

          {!loadingHist && history && history.series.length === 0 && (
            <div className="bs-empty">
              No historical data available for this contract via{" "}
              {selectedRow.venue}.
              {selectedRow.venue === "deribit" &&
                " Deribit history requires Amberdata key."}
            </div>
          )}
        </section>
      )}
    </div>
  );
}
