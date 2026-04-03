"use client";

/**
 * Dated Futures Basis — rebuilt on unified /api/opportunities data.
 *
 * Section 1 — Near-expiry summary cards (one per venue, selected asset)
 * Section 2 — Term structure chart   (DTE × annualised basis, per venue)
 * Section 3 — Basis snapshot table   (sorted by ann basis desc, clickable)
 * Section 4 — Historical basis chart (loaded on row click)
 *
 * The old /api/basis/snapshot and /api/basis/history endpoints remain on the
 * backend for backward compatibility but are no longer called here.
 */

import dynamic from "next/dynamic";
import { useCallback, useEffect, useMemo, useState } from "react";
import { fetchOpportunities, fetchOpportunityHistory } from "@/lib/api";
import type { MarketOpportunity, OpportunityRatePoint } from "@/types/api";

const ReactECharts = dynamic(() => import("echarts-for-react"), { ssr: false });

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const SYMBOLS = ["BTC", "ETH", "SOL"] as const;
type BasisSymbol = (typeof SYMBOLS)[number];

const ALL_VENUES = ["DERIBIT", "BINANCE", "OKX", "BYBIT", "CME"] as const;

const VENUE_LABELS: Record<string, string> = {
  DERIBIT: "Deribit",
  BINANCE: "Binance",
  OKX: "OKX",
  BYBIT: "Bybit",
  CME: "CME",
};

const VENUE_COLORS: Record<string, string> = {
  DERIBIT: "#6366f1",
  BINANCE: "#f59e0b",
  OKX: "#22c55e",
  BYBIT: "#3b82f6",
  CME: "#f97316",
};

// ---------------------------------------------------------------------------
// Formatters
// ---------------------------------------------------------------------------

function fmtBasis(v: number | null | undefined, decimals = 2): string {
  if (v == null) return "—";
  const sign = v > 0 ? "+" : "";
  return `${sign}${v.toFixed(decimals)}%`;
}

function fmtUsd(v: number | null | undefined): string {
  if (v == null) return "—";
  const a = Math.abs(v);
  if (a >= 1e9) return `$${(v / 1e9).toFixed(2)}B`;
  if (a >= 1e6) return `$${(v / 1e6).toFixed(1)}M`;
  if (a >= 1e3) return `$${(v / 1e3).toFixed(0)}K`;
  return `$${v.toFixed(2)}`;
}

function fmtPrice(v: number | null | undefined): string {
  if (v == null) return "—";
  return `$${v.toLocaleString(undefined, { maximumFractionDigits: 2 })}`;
}

function fmtDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    year: "2-digit",
  });
}

function basisColor(v: number | null | undefined): string {
  if (v == null) return "var(--text-secondary)";
  if (v > 12) return "#ef4444";
  if (v > 6) return "#f97316";
  if (v > 2) return "#f59e0b";
  if (v >= 0) return "#22c55e";
  return "#6366f1"; // backwardation
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Pull OI from the LiquidityInfo embedded record. */
function oppOiUsd(opp: MarketOpportunity): number | null {
  return (
    ((opp.liquidity as Record<string, unknown>)
      ?.available_liquidity_usd as number | null) ?? null
  );
}

/**
 * Parse futures and spot prices from reward_breakdown[0].notes.
 * Notes format: "Basis locked at entry; futures=65000.00, spot=64000.00, DTE=30"
 */
function parsePrices(opp: MarketOpportunity): {
  futures: number | null;
  spot: number | null;
} {
  const notes = (opp.reward_breakdown?.[0] as { notes?: string } | undefined)?.notes;
  if (!notes) return { futures: null, spot: null };
  const fMatch = notes.match(/futures=([\d.]+)/);
  const sMatch = notes.match(/spot=([\d.]+)/);
  return {
    futures: fMatch ? parseFloat(fMatch[1]) : null,
    spot: sMatch ? parseFloat(sMatch[1]) : null,
  };
}


// ---------------------------------------------------------------------------
// Near-expiry summary cards
// ---------------------------------------------------------------------------

function SummaryCards({
  opps,
  symbol,
}: {
  opps: MarketOpportunity[];
  symbol: BasisSymbol;
}) {
  const assetOpps = opps.filter(
    (o) => o.asset_symbol.toUpperCase() === symbol,
  );

  // Per venue: pick the contract with fewest days to maturity
  const nearExpiry = ALL_VENUES.map((v) => {
    const venueOpps = assetOpps.filter((o) => o.venue === v);
    if (venueOpps.length === 0) return { venue: v, opp: null };
    const nearest = venueOpps.reduce((best, o) =>
      (o.days_to_maturity ?? Infinity) < (best.days_to_maturity ?? Infinity)
        ? o
        : best,
    );
    return { venue: v, opp: nearest };
  }).filter((x) => x.opp !== null);

  if (nearExpiry.length === 0) {
    return (
      <div className="bs-empty">No basis data available for {symbol}</div>
    );
  }

  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: `repeat(${nearExpiry.length}, 1fr)`,
        gap: "0.75rem",
      }}
    >
      {nearExpiry.map(({ venue, opp }) => {
        const basis = opp!.total_apy_pct;
        const dte = opp!.days_to_maturity;
        return (
          <div
            key={venue}
            style={{
              background: "var(--surface)",
              border: "1px solid var(--border)",
              borderRadius: "var(--radius)",
              padding: "0.85rem 1rem",
              borderLeft: `3px solid ${VENUE_COLORS[venue] ?? "#888"}`,
            }}
          >
            <div
              style={{
                fontFamily: "var(--font-mono)",
                fontSize: "10px",
                letterSpacing: "0.07em",
                color: "var(--text-muted)",
                textTransform: "uppercase",
                marginBottom: "0.3rem",
              }}
            >
              {VENUE_LABELS[venue] ?? venue}
              {dte != null && (
                <span style={{ marginLeft: "0.4rem", opacity: 0.7 }}>
                  {Math.round(dte)}d
                </span>
              )}
            </div>
            <div
              style={{
                fontFamily: "var(--font-mono)",
                fontSize: "20px",
                fontWeight: 600,
                color: basisColor(basis),
                lineHeight: 1.2,
              }}
            >
              {fmtBasis(basis)}
            </div>
            <div
              style={{
                fontSize: "11px",
                color: "var(--text-muted)",
                marginTop: "0.2rem",
                fontFamily: "var(--font-mono)",
              }}
            >
              {opp!.market_id}
            </div>
          </div>
        );
      })}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Term structure chart
// ---------------------------------------------------------------------------

function buildTermStructureOption(
  opps: MarketOpportunity[],
  activeVenues: Set<string>,
) {
  const byVenue: Record<string, Array<{ dte: number; basis: number; contract: string }>> = {};

  for (const o of opps) {
    if (!activeVenues.has(o.venue)) continue;
    if (o.days_to_maturity == null) continue;
    byVenue[o.venue] ??= [];
    byVenue[o.venue].push({
      dte: o.days_to_maturity,
      basis: o.total_apy_pct,
      contract: o.market_id,
    });
  }

  const series = Object.entries(byVenue).flatMap(([venue, pts]) => {
    const sorted = [...pts].sort((a, b) => a.dte - b.dte);
    const color = VENUE_COLORS[venue] ?? "#888";
    const label = VENUE_LABELS[venue] ?? venue;
    const data = sorted.map((p) => ({
      value: [p.dte, p.basis],
      contract: p.contract,
    }));

    return [
      {
        name: label,
        type: "line" as const,
        showSymbol: false,
        smooth: false,
        lineStyle: { color, width: 1.5, type: "dashed" as const },
        itemStyle: { color },
        data,
        tooltip: { show: false },
      },
      {
        name: label,
        type: "scatter" as const,
        symbolSize: 9,
        itemStyle: { color },
        data,
        showInLegend: false,
        legendHoverLink: true,
      },
    ];
  });

  return {
    backgroundColor: "transparent",
    tooltip: {
      trigger: "item",
      backgroundColor: "#1e2328",
      borderColor: "#2a3040",
      textStyle: { color: "#e8eaed", fontSize: 12 },
      formatter: (params: { data: { value: [number, number]; contract: string } }) => {
        const d = params.data;
        return [
          `<strong>${d.contract}</strong>`,
          `DTE: ${d.value[0].toFixed(0)}d`,
          `Ann Basis: ${d.value[1].toFixed(2)}%`,
        ].join("<br/>");
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
      nameLocation: "end" as const,
      axisLine: { lineStyle: { color: "#1e2328" } },
      axisLabel: { color: "#8b9099", fontSize: 11 },
      splitLine: { lineStyle: { color: "#1e2328" } },
    },
    yAxis: {
      name: "Ann Basis %",
      nameTextStyle: { color: "#8b9099", fontSize: 11 },
      axisLine: { lineStyle: { color: "#1e2328" } },
      axisLabel: {
        color: "#8b9099",
        fontSize: 11,
        formatter: (v: number) => `${v.toFixed(1)}%`,
      },
      splitLine: { lineStyle: { color: "#1e2328" } },
    },
    series,
  };
}

// ---------------------------------------------------------------------------
// Historical chart
// ---------------------------------------------------------------------------

function buildHistoryOption(pts: OpportunityRatePoint[], contract: string) {
  const data = pts
    .filter((p) => p.total_apy_pct != null)
    .map((p): [number, number] => [
      new Date(p.snapshot_at).getTime(),
      p.total_apy_pct,
    ]);

  return {
    backgroundColor: "transparent",
    tooltip: {
      trigger: "axis",
      backgroundColor: "#1e2328",
      borderColor: "#2a3040",
      textStyle: { color: "#e8eaed", fontSize: 12 },
      formatter: (params: { value: [number, number] }[]) => {
        const p = params[0];
        const date = new Date(p.value[0]).toLocaleDateString();
        return [
          `<strong>${date}</strong>`,
          `${contract}: ${p.value[1].toFixed(2)}%`,
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
      name: "Ann Basis %",
      nameTextStyle: { color: "#8b9099", fontSize: 11 },
      axisLine: { lineStyle: { color: "#1e2328" } },
      axisLabel: {
        color: "#8b9099",
        fontSize: 11,
        formatter: (v: number) => `${v.toFixed(1)}%`,
      },
      splitLine: { lineStyle: { color: "#1e2328" } },
    },
    series: [
      {
        type: "line",
        data,
        showSymbol: false,
        lineStyle: { color: "#3b82f6", width: 1.5 },
        areaStyle: { color: "rgba(59,130,246,0.07)" },
      },
    ],
  };
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function BasisPage() {
  const [opps, setOpps] = useState<MarketOpportunity[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [symbol, setSymbol] = useState<BasisSymbol>("BTC");
  const [activeVenues, setActiveVenues] = useState<Set<string>>(
    new Set(ALL_VENUES),
  );

  // History state
  const [selectedOpp, setSelectedOpp] = useState<MarketOpportunity | null>(
    null,
  );
  const [historyPts, setHistoryPts] = useState<OpportunityRatePoint[]>([]);
  const [historyDays, setHistoryDays] = useState<14 | 30 | 89>(30);
  const [historyLoading, setHistoryLoading] = useState(false);

  // Fetch all basis trade opportunities once
  useEffect(() => {
    setLoading(true);
    fetchOpportunities({ type: "BASIS_TRADE", limit: 500 })
      .then((resp) => setOpps(resp.data))
      .catch(() => setError("Failed to load basis data"))
      .finally(() => setLoading(false));
  }, []);

  // Load history when selected opp or days changes
  const loadHistory = useCallback(
    async (opp: MarketOpportunity, days: number) => {
      setHistoryLoading(true);
      setHistoryPts([]);
      try {
        const pts = await fetchOpportunityHistory(opp.opportunity_id, days);
        setHistoryPts(pts);
      } catch {
        // silently show empty
      } finally {
        setHistoryLoading(false);
      }
    },
    [],
  );

  const handleRowClick = useCallback(
    (opp: MarketOpportunity) => {
      setSelectedOpp(opp);
      loadHistory(opp, historyDays);
    },
    [historyDays, loadHistory],
  );

  const handleHistoryDaysChange = (d: 14 | 30 | 89) => {
    setHistoryDays(d);
    if (selectedOpp) loadHistory(selectedOpp, d);
  };

  // Filter to selected symbol + active venues, sorted by basis desc
  const filteredOpps = useMemo(
    () =>
      opps
        .filter(
          (o) =>
            o.asset_symbol.toUpperCase() === symbol &&
            activeVenues.has(o.venue),
        )
        .sort((a, b) => b.total_apy_pct - a.total_apy_pct),
    [opps, symbol, activeVenues],
  );

  const termOption = useMemo(
    () =>
      filteredOpps.length > 0
        ? buildTermStructureOption(filteredOpps, activeVenues)
        : null,
    [filteredOpps, activeVenues],
  );

  const historyOption = useMemo(
    () =>
      historyPts.length > 0 && selectedOpp
        ? buildHistoryOption(historyPts, selectedOpp.market_id)
        : null,
    [historyPts, selectedOpp],
  );

  const toggleVenue = (v: string) => {
    setActiveVenues((prev) => {
      const next = new Set(prev);
      if (next.has(v)) next.delete(v);
      else next.add(v);
      return next;
    });
  };

  const asOf = opps[0]?.last_updated_at;

  return (
    <div className="bs-page">
      {/* ── Header + controls ─────────────────────────────────────────────── */}
      <div className="bs-header">
        <div className="bs-title-block">
          <h1 className="bs-title">Dated Futures Basis</h1>
          {asOf && (
            <span className="bs-as-of">
              as of {new Date(asOf).toLocaleTimeString()}
            </span>
          )}
        </div>

        <div className="bs-controls">
          {/* Asset */}
          <div className="bs-control-group">
            <span className="bs-control-label">Asset</span>
            {SYMBOLS.map((s) => (
              <button
                key={s}
                className={`bs-btn ${symbol === s ? "bs-btn--active" : ""}`}
                onClick={() => {
                  setSymbol(s);
                  setSelectedOpp(null);
                  setHistoryPts([]);
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
                    ? {
                        borderColor: VENUE_COLORS[v],
                        color: VENUE_COLORS[v],
                        background: `${VENUE_COLORS[v]}18`,
                      }
                    : {}
                }
                onClick={() => toggleVenue(v)}
              >
                {VENUE_LABELS[v]}
              </button>
            ))}
          </div>

          <button
            className="bs-btn bs-btn--refresh"
            onClick={() => {
              setLoading(true);
              fetchOpportunities({ type: "BASIS_TRADE", limit: 500 })
                .then((resp) => setOpps(resp.data))
                .catch(() => {})
                .finally(() => setLoading(false));
            }}
          >
            ↺ Refresh
          </button>
        </div>
      </div>

      {loading && <div className="bs-loading">Loading basis data…</div>}
      {error && <div className="bs-error">{error}</div>}

      {/* ── Summary cards ─────────────────────────────────────────────────── */}
      {!loading && <SummaryCards opps={opps} symbol={symbol} />}

      {/* ── Term structure chart ──────────────────────────────────────────── */}
      {termOption && (
        <section className="bs-section bs-section--chart">
          <h2 className="bs-section-title">Term Structure — {symbol}</h2>
          <ReactECharts
            option={termOption}
            style={{ height: 300 }}
            notMerge
          />
        </section>
      )}

      {/* ── Basis snapshot table ──────────────────────────────────────────── */}
      {filteredOpps.length > 0 && (
        <section className="bs-section">
          <h2 className="bs-section-title">
            Basis Snapshot
            <span className="bs-section-hint">click a row to load history</span>
          </h2>
          <div className="bs-table-wrap">
            <table className="bs-table">
              <thead>
                <tr>
                  <th>Asset</th>
                  <th>Venue</th>
                  <th>Contract</th>
                  <th>Expiry</th>
                  <th>DTE</th>
                  <th>Ann Basis</th>
                  <th>Spot Price</th>
                  <th>Futures Price</th>
                  <th>OI (USD)</th>
                </tr>
              </thead>
              <tbody>
                {filteredOpps.map((o) => {
                  const { futures, spot } = parsePrices(o);
                  const isSelected =
                    selectedOpp?.opportunity_id === o.opportunity_id;
                  return (
                    <tr
                      key={o.opportunity_id}
                      className={`bs-tr ${isSelected ? "bs-tr--selected" : ""}`}
                      style={{
                        cursor: "pointer",
                        background: isSelected
                          ? "rgba(59,130,246,0.07)"
                          : undefined,
                      }}
                      onClick={() => handleRowClick(o)}
                    >
                      <td>
                        <span
                          style={{
                            fontFamily: "var(--font-mono)",
                            fontWeight: 600,
                            color: "var(--text-primary)",
                          }}
                        >
                          {o.asset_symbol}
                        </span>
                      </td>
                      <td>
                        <span
                          style={{
                            display: "inline-flex",
                            alignItems: "center",
                            gap: "0.35rem",
                          }}
                        >
                          <span
                            style={{
                              display: "inline-block",
                              width: "7px",
                              height: "7px",
                              borderRadius: "50%",
                              background: VENUE_COLORS[o.venue] ?? "#888",
                              flexShrink: 0,
                            }}
                          />
                          {VENUE_LABELS[o.venue] ?? o.venue}
                        </span>
                      </td>
                      <td
                        style={{
                          fontFamily: "var(--font-mono)",
                          color: "var(--text-primary)",
                        }}
                      >
                        {o.market_id}
                      </td>
                      <td
                        style={{
                          fontFamily: "var(--font-mono)",
                          color: "var(--text-secondary)",
                        }}
                      >
                        {fmtDate(o.maturity_date)}
                      </td>
                      <td
                        style={{
                          fontFamily: "var(--font-mono)",
                          color: "var(--text-secondary)",
                        }}
                      >
                        {o.days_to_maturity != null
                          ? `${Math.round(o.days_to_maturity)}d`
                          : "—"}
                      </td>
                      <td
                        style={{
                          fontFamily: "var(--font-mono)",
                          fontWeight: 600,
                          color: basisColor(o.total_apy_pct),
                        }}
                      >
                        {fmtBasis(o.total_apy_pct)}
                      </td>
                      <td
                        style={{
                          fontFamily: "var(--font-mono)",
                          color: "var(--text-secondary)",
                        }}
                      >
                        {fmtPrice(spot)}
                      </td>
                      <td
                        style={{
                          fontFamily: "var(--font-mono)",
                          color: "var(--text-secondary)",
                        }}
                      >
                        {fmtPrice(futures)}
                      </td>
                      <td
                        style={{
                          fontFamily: "var(--font-mono)",
                          color: "var(--text-secondary)",
                        }}
                      >
                        {fmtUsd(oppOiUsd(o))}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {!loading && filteredOpps.length === 0 && (
        <div className="bs-empty">
          No basis data for {symbol} with selected venues
        </div>
      )}

      {/* ── Historical basis chart ────────────────────────────────────────── */}
      {selectedOpp && (
        <section className="bs-section">
          <div className="bs-hist-header" style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "0.75rem" }}>
            <h2 className="bs-section-title" style={{ margin: 0 }}>
              Historical Basis —{" "}
              <span style={{ color: VENUE_COLORS[selectedOpp.venue] ?? "#888" }}>
                {VENUE_LABELS[selectedOpp.venue] ?? selectedOpp.venue}
              </span>{" "}
              {selectedOpp.market_id}
            </h2>
            <div style={{ display: "flex", gap: "0.35rem" }}>
              {([14, 30, 89] as const).map((d) => (
                <button
                  key={d}
                  className={`bs-btn ${historyDays === d ? "bs-btn--active" : ""}`}
                  onClick={() => handleHistoryDaysChange(d)}
                >
                  {d}d
                </button>
              ))}
            </div>
          </div>

          {historyLoading && <div className="bs-loading">Loading history…</div>}

          {!historyLoading && historyOption && (
            <ReactECharts
              option={historyOption}
              style={{ height: 260 }}
              notMerge
            />
          )}

          {!historyLoading && !historyOption && (
            <div className="bs-empty">
              No historical snapshots stored for {selectedOpp.market_id} yet
            </div>
          )}
        </section>
      )}
    </div>
  );
}
