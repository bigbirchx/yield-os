"use client";

/**
 * Perpetual Funding Rates — rebuilt on unified /api/opportunities data.
 *
 * Section 1 — Summary cards (highest rate, per-asset averages)
 * Section 2 — Snapshot table (all FUNDING_RATE opportunities)
 * Section 3 — Historical chart (venue comparison, asset + range selector)
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
type ChartSymbol = (typeof SYMBOLS)[number];

const VENUE_LABELS: Record<string, string> = {
  BINANCE: "Binance",
  OKX: "OKX",
  BYBIT: "Bybit",
  DERIBIT: "Deribit",
  BULLISH: "Bullish",
};

const VENUE_COLORS: Record<string, string> = {
  BINANCE: "#F0B90B",
  OKX: "#aaaaaa",
  BYBIT: "#E07B39",
  DERIBIT: "#00C9A7",
  BULLISH: "#9B72F6",
};

// ---------------------------------------------------------------------------
// Formatters
// ---------------------------------------------------------------------------

function fmtRate(v: number | null | undefined): string {
  if (v == null) return "—";
  const sign = v > 0 ? "+" : "";
  return `${sign}${v.toFixed(2)}%`;
}

function fmtUsd(v: number | null | undefined): string {
  if (v == null) return "—";
  const a = Math.abs(v);
  if (a >= 1e9) return `$${(v / 1e9).toFixed(2)}B`;
  if (a >= 1e6) return `$${(v / 1e6).toFixed(1)}M`;
  if (a >= 1e3) return `$${(v / 1e3).toFixed(0)}K`;
  return `$${v.toFixed(0)}`;
}

function rateColor(v: number | null | undefined): string {
  if (v == null) return "var(--text-secondary)";
  if (v > 0) return "var(--green)";
  if (v < 0) return "var(--red)";
  return "var(--text-secondary)";
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Compute 7-day average from the embedded historical_rates_7d field. */
function avg7d(opp: MarketOpportunity): number | null {
  const hist = (opp as unknown as Record<string, unknown>).historical_rates_7d as
    | Array<{ date: string; value: number }>
    | null
    | undefined;
  if (!hist || hist.length === 0) return null;
  return hist.reduce((s, p) => s + p.value, 0) / hist.length;
}

/** Pull OI from the LiquidityInfo record. */
function oppOiUsd(opp: MarketOpportunity): number | null {
  return (opp.liquidity as Record<string, unknown>)
    ?.available_liquidity_usd as number | null ?? null;
}

// ---------------------------------------------------------------------------
// Summary cards
// ---------------------------------------------------------------------------

interface Summary {
  highest: MarketOpportunity;
  avgBtc: number | null;
  avgEth: number | null;
  avgSol: number | null;
}

function computeSummary(opps: MarketOpportunity[]): Summary | null {
  if (opps.length === 0) return null;
  const highest = opps.reduce((best, o) =>
    o.total_apy_pct > best.total_apy_pct ? o : best,
  );
  const avgFor = (sym: string) => {
    const hits = opps.filter(
      (o) => o.asset_symbol.toUpperCase() === sym,
    );
    if (hits.length === 0) return null;
    return hits.reduce((s, o) => s + o.total_apy_pct, 0) / hits.length;
  };
  return {
    highest,
    avgBtc: avgFor("BTC"),
    avgEth: avgFor("ETH"),
    avgSol: avgFor("SOL"),
  };
}

function SummaryCards({ opps }: { opps: MarketOpportunity[] }) {
  const s = computeSummary(opps);
  if (!s) return null;

  const cards = [
    {
      label: "Highest Rate",
      value: fmtRate(s.highest.total_apy_pct),
      sub: `${VENUE_LABELS[s.highest.venue] ?? s.highest.venue} · ${s.highest.asset_symbol}`,
      color: rateColor(s.highest.total_apy_pct),
    },
    {
      label: "BTC Avg (All Venues)",
      value: fmtRate(s.avgBtc),
      sub: "annualised perpetual rate",
      color: rateColor(s.avgBtc),
    },
    {
      label: "ETH Avg (All Venues)",
      value: fmtRate(s.avgEth),
      sub: "annualised perpetual rate",
      color: rateColor(s.avgEth),
    },
    {
      label: "SOL Avg (All Venues)",
      value: fmtRate(s.avgSol),
      sub: "annualised perpetual rate",
      color: rateColor(s.avgSol),
    },
  ];

  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: "repeat(4, 1fr)",
        gap: "0.75rem",
      }}
    >
      {cards.map((c) => (
        <div
          key={c.label}
          style={{
            background: "var(--surface)",
            border: "1px solid var(--border)",
            borderRadius: "var(--radius)",
            padding: "0.85rem 1rem",
          }}
        >
          <div
            style={{
              fontFamily: "var(--font-mono)",
              fontSize: "10px",
              letterSpacing: "0.07em",
              color: "var(--text-muted)",
              textTransform: "uppercase",
              marginBottom: "0.35rem",
            }}
          >
            {c.label}
          </div>
          <div
            style={{
              fontFamily: "var(--font-mono)",
              fontSize: "20px",
              fontWeight: 600,
              color: c.color,
              lineHeight: 1.2,
            }}
          >
            {c.value}
          </div>
          <div
            style={{
              fontSize: "11px",
              color: "var(--text-muted)",
              marginTop: "0.25rem",
            }}
          >
            {c.sub}
          </div>
        </div>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Historical chart
// ---------------------------------------------------------------------------

interface ChartSeries {
  venue: string;
  points: Array<[number, number]>; // [epoch ms, rate pct]
}

function buildChartOption(series: ChartSeries[]) {
  return {
    backgroundColor: "transparent",
    tooltip: {
      trigger: "axis",
      backgroundColor: "#1e2328",
      borderColor: "#2a3040",
      textStyle: { color: "#e8eaed", fontSize: 12 },
      formatter: (params: { value: [number, number]; seriesName: string; color: string }[]) => {
        const ts = params[0]?.value?.[0];
        const date = ts ? new Date(ts).toLocaleDateString() : "";
        const lines = params.map(
          (p) =>
            `<span style="color:${p.color}">●</span> ${p.seriesName}: ${p.value[1].toFixed(2)}%`,
        );
        return [`<strong>${date}</strong>`, ...lines].join("<br/>");
      },
    },
    legend: {
      top: 8,
      right: 16,
      textStyle: { color: "#8b9099", fontSize: 11 },
    },
    grid: { left: 68, right: 20, top: 48, bottom: 40 },
    xAxis: {
      type: "time",
      axisLine: { lineStyle: { color: "#1e2328" } },
      axisLabel: { color: "#8b9099", fontSize: 11 },
      splitLine: { lineStyle: { color: "#1e2328" } },
    },
    yAxis: {
      name: "Ann Rate %",
      nameTextStyle: { color: "#8b9099", fontSize: 10 },
      axisLine: { lineStyle: { color: "#1e2328" } },
      axisLabel: {
        color: "#8b9099",
        fontSize: 11,
        formatter: (v: number) => `${v.toFixed(1)}%`,
      },
      splitLine: { lineStyle: { color: "#1e2328" } },
    },
    series: series.map(({ venue, points }) => ({
      name: VENUE_LABELS[venue] ?? venue,
      type: "line",
      data: points,
      showSymbol: false,
      lineStyle: { color: VENUE_COLORS[venue] ?? "#888", width: 1.5 },
      itemStyle: { color: VENUE_COLORS[venue] ?? "#888" },
    })),
  };
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function FundingPage() {
  const [opps, setOpps] = useState<MarketOpportunity[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Chart controls
  const [chartSymbol, setChartSymbol] = useState<ChartSymbol>("BTC");
  const [chartDays, setChartDays] = useState<7 | 30 | 90>(30);
  const [chartSeries, setChartSeries] = useState<ChartSeries[]>([]);
  const [chartLoading, setChartLoading] = useState(false);

  // Fetch all funding-rate opportunities once on mount
  useEffect(() => {
    setLoading(true);
    fetchOpportunities({ type: "FUNDING_RATE", limit: 200 })
      .then((resp) => {
        setOpps(resp.data);
      })
      .catch(() => setError("Failed to load funding rates"))
      .finally(() => setLoading(false));
  }, []);

  // Rebuild chart whenever opps, symbol, or days changes
  const loadChart = useCallback(async () => {
    const matching = opps.filter(
      (o) => o.asset_symbol.toUpperCase() === chartSymbol,
    );
    if (matching.length === 0) {
      setChartSeries([]);
      return;
    }

    setChartLoading(true);
    setChartSeries([]);

    // For 7d: use embedded historical_rates_7d to avoid extra round-trips
    if (chartDays === 7) {
      const series: ChartSeries[] = matching
        .map((o) => {
          const hist = (o as unknown as Record<string, unknown>).historical_rates_7d as
            | Array<{ date: string; value: number }>
            | null
            | undefined;
          if (!hist || hist.length === 0) return null;
          return {
            venue: o.venue,
            points: hist.map((p): [number, number] => [
              new Date(`${p.date}T12:00:00Z`).getTime(),
              p.value,
            ]),
          };
        })
        .filter((s): s is ChartSeries => s !== null);
      setChartSeries(series);
      setChartLoading(false);
      return;
    }

    // 30d / 90d: fetch from the history endpoint
    try {
      const results = await Promise.all(
        matching.map((o) =>
          fetchOpportunityHistory(o.opportunity_id, chartDays).then(
            (pts) => ({ venue: o.venue, pts }),
          ),
        ),
      );
      const series: ChartSeries[] = results
        .filter((r) => r.pts.length > 0)
        .map(({ venue, pts }) => ({
          venue,
          points: pts.map(
            (p: OpportunityRatePoint): [number, number] => [
              new Date(p.snapshot_at).getTime(),
              p.total_apy_pct,
            ],
          ),
        }));
      setChartSeries(series);
    } catch {
      // chart silently shows empty
    } finally {
      setChartLoading(false);
    }
  }, [opps, chartSymbol, chartDays]);

  useEffect(() => {
    if (opps.length > 0) loadChart();
  }, [opps, chartSymbol, chartDays, loadChart]);

  const chartOption = useMemo(
    () => (chartSeries.length > 0 ? buildChartOption(chartSeries) : null),
    [chartSeries],
  );

  // Sort table: by asset symbol, then venue
  const sortedOpps = useMemo(
    () =>
      [...opps].sort((a, b) => {
        const symCmp = a.asset_symbol.localeCompare(b.asset_symbol);
        if (symCmp !== 0) return symCmp;
        return a.venue.localeCompare(b.venue);
      }),
    [opps],
  );

  return (
    <div className="fn-page">
      {/* Header */}
      <div
        style={{
          display: "flex",
          alignItems: "baseline",
          gap: "0.75rem",
          paddingBottom: "0.25rem",
        }}
      >
        <h1
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: "11px",
            fontWeight: 700,
            letterSpacing: "0.1em",
            color: "var(--text-secondary)",
            textTransform: "uppercase",
          }}
        >
          Perpetual Funding Rates
        </h1>
        {!loading && (
          <span
            style={{
              fontFamily: "var(--font-mono)",
              fontSize: "10px",
              color: "var(--text-muted)",
            }}
          >
            {opps.length} contracts
          </span>
        )}
      </div>

      {/* Summary cards */}
      {!loading && opps.length > 0 && <SummaryCards opps={opps} />}

      {/* Loading / error states */}
      {loading && <div className="fn-loading">Loading funding rates…</div>}
      {error && (
        <div
          style={{
            color: "var(--red)",
            fontSize: "12px",
            padding: "0.5rem 0.75rem",
            background: "var(--red-dim)",
            border: "1px solid var(--red)",
            borderRadius: "var(--radius)",
          }}
        >
          {error}
        </div>
      )}

      {/* Snapshot table */}
      {!loading && sortedOpps.length > 0 && (
        <div
          style={{
            background: "var(--surface)",
            border: "1px solid var(--border)",
            borderRadius: "var(--radius)",
            overflow: "hidden",
          }}
        >
          <div
            style={{
              padding: "0.6rem 1rem",
              borderBottom: "1px solid var(--border-subtle)",
              fontFamily: "var(--font-mono)",
              fontSize: "10px",
              fontWeight: 700,
              letterSpacing: "0.08em",
              color: "var(--text-muted)",
              textTransform: "uppercase",
            }}
          >
            Current Snapshot
          </div>
          <table className="fn-table">
            <thead>
              <tr>
                <th>Asset</th>
                <th>Venue</th>
                <th>Current Rate (Ann %)</th>
                <th>7d Avg</th>
                <th>Open Interest</th>
                <th>Direction</th>
              </tr>
            </thead>
            <tbody>
              {sortedOpps.map((o) => {
                const rate = o.total_apy_pct;
                const sevenDay = avg7d(o);
                const oi = oppOiUsd(o);
                const positive = rate >= 0;
                return (
                  <tr key={o.opportunity_id}>
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
                      <span className="fn-exch-name">
                        <span
                          className="fn-dot"
                          style={{
                            background: VENUE_COLORS[o.venue] ?? "#888",
                          }}
                        />
                        {VENUE_LABELS[o.venue] ?? o.venue}
                      </span>
                    </td>
                    <td
                      style={{
                        fontFamily: "var(--font-mono)",
                        fontWeight: 600,
                        color: rateColor(rate),
                      }}
                    >
                      {fmtRate(rate)}
                    </td>
                    <td
                      style={{
                        fontFamily: "var(--font-mono)",
                        color: rateColor(sevenDay),
                      }}
                    >
                      {sevenDay != null ? fmtRate(sevenDay) : "—"}
                    </td>
                    <td
                      style={{
                        fontFamily: "var(--font-mono)",
                        color: "var(--text-secondary)",
                      }}
                    >
                      {fmtUsd(oi)}
                    </td>
                    <td>
                      <span
                        style={{
                          display: "inline-block",
                          fontSize: "10px",
                          padding: "1px 6px",
                          borderRadius: "3px",
                          fontFamily: "var(--font-mono)",
                          letterSpacing: "0.04em",
                          background: positive
                            ? "rgba(34,197,94,0.1)"
                            : "rgba(239,68,68,0.1)",
                          color: positive ? "var(--green)" : "var(--red)",
                          border: `1px solid ${positive ? "rgba(34,197,94,0.2)" : "rgba(239,68,68,0.2)"}`,
                        }}
                      >
                        {positive ? "LONGS PAY" : "SHORTS PAY"}
                      </span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {/* Historical chart */}
      {!loading && opps.length > 0 && (
        <div
          style={{
            background: "var(--surface)",
            border: "1px solid var(--border)",
            borderRadius: "var(--radius)",
            padding: "1rem 1.25rem",
          }}
        >
          {/* Chart header + controls */}
          <div
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              flexWrap: "wrap",
              gap: "0.5rem",
              marginBottom: "0.75rem",
            }}
          >
            <span
              style={{
                fontFamily: "var(--font-mono)",
                fontSize: "10px",
                fontWeight: 700,
                letterSpacing: "0.08em",
                color: "var(--text-secondary)",
                textTransform: "uppercase",
              }}
            >
              Historical Funding Rates
            </span>

            <div className="fn-controls" style={{ padding: 0, position: "static", background: "transparent", border: "none" }}>
              <div className="fn-control-group">
                <span className="fn-control-label">Asset</span>
                {SYMBOLS.map((s) => (
                  <button
                    key={s}
                    className={`fn-chip ${chartSymbol === s ? "fn-chip--active" : ""}`}
                    onClick={() => setChartSymbol(s)}
                  >
                    {s}
                  </button>
                ))}
              </div>
              <div className="fn-control-group">
                <span className="fn-control-label">Range</span>
                {([7, 30, 90] as const).map((d) => (
                  <button
                    key={d}
                    className={`fn-chip ${chartDays === d ? "fn-chip--active" : ""}`}
                    onClick={() => setChartDays(d)}
                  >
                    {d}d
                  </button>
                ))}
              </div>
            </div>
          </div>

          {chartLoading && <div className="fn-loading">Loading chart…</div>}

          {!chartLoading && chartOption && (
            <ReactECharts option={chartOption} style={{ height: 280 }} notMerge />
          )}

          {!chartLoading && !chartOption && (
            <div className="fn-empty">
              No historical data available for {chartSymbol}
              {chartDays > 7 ? " — historical snapshots may not be stored yet" : ""}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
