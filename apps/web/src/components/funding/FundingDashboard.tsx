"use client";

/**
 * Perpetual Funding Rate Dashboard
 *
 * Section 1 — Exchange snapshot table
 * Section 2 — Controls (symbol, exchanges, blend, days, MA periods)
 * Section 3 — Time-series chart (ECharts)
 * Section 4 — Distribution analysis (histogram + KDE + box plot)
 * Section 5 — Coinglass cross-check strip
 */

import dynamic from "next/dynamic";
import { useCallback, useEffect, useMemo, useState } from "react";

// ECharts must be client-only (no SSR)
const ReactECharts = dynamic(() => import("echarts-for-react"), { ssr: false });

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface ExchangeSnap {
  live_apr: number | null;
  last_apr: number | null;
  funding_interval_hours: number | null;
  oi_coin: number | null;
  oi_usd: number | null;
  volume_coin_24h: number | null;
  volume_usd_24h: number | null;
  ma_7d_apr: number | null;
  ma_30d_apr: number | null;
}

interface FundingSnapshotResp {
  symbol: string;
  as_of: string;
  exchanges: Record<string, ExchangeSnap>;
  blended: {
    equal_weighted_apr: number | null;
    oi_weighted_apr: number | null;
    volume_weighted_apr: number | null;
  };
  coinglass: {
    binance_apr: number | null;
    okx_apr: number | null;
    bybit_apr: number | null;
  };
}

interface SeriesPoint {
  date: string;
  value: number;
}

interface HistoryResp {
  symbol: string;
  exchange: string;
  series: SeriesPoint[];
  blend_series?: {
    equal_weighted: SeriesPoint[];
    oi_weighted: SeriesPoint[];
    volume_weighted: SeriesPoint[];
  };
}

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const SYMBOLS = ["BTC", "ETH", "SOL"];
const EXCHANGES = ["binance", "okx", "bybit", "deribit", "bullish"] as const;
type Exchange = (typeof EXCHANGES)[number];

const EXCHANGE_LABELS: Record<string, string> = {
  binance: "Binance",
  okx: "OKX",
  bybit: "Bybit",
  deribit: "Deribit",
  bullish: "Bullish",
};

const EXCHANGE_COLORS: Record<string, string> = {
  binance: "#F0B90B",
  okx: "#aaaaaa",
  bybit: "#E07B39",
  deribit: "#00C9A7",
  bullish: "#9B72F6",
  blend_equal: "#3b82f6",
  blend_oi: "#06b6d4",
  blend_vol: "#8b5cf6",
};

const API_URL =
  process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

// ---------------------------------------------------------------------------
// Fetch helpers
// ---------------------------------------------------------------------------

async function fetchSnapshot(symbol: string): Promise<FundingSnapshotResp | null> {
  try {
    const r = await fetch(`${API_URL}/api/funding/snapshot?symbol=${symbol}`, {
      cache: "no-store",
    });
    if (!r.ok) return null;
    return r.json();
  } catch {
    return null;
  }
}

async function fetchHistory(
  symbol: string,
  exchange: string,
  days: number,
  blend: boolean,
  warmupDays = 0
): Promise<HistoryResp | null> {
  try {
    const r = await fetch(
      `${API_URL}/api/funding/history?symbol=${symbol}&exchange=${exchange}&days=${days}&blend=${blend}&warmup_days=${warmupDays}`,
      { cache: "no-store" }
    );
    if (!r.ok) return null;
    return r.json();
  } catch {
    return null;
  }
}

// ---------------------------------------------------------------------------
// Formatters
// ---------------------------------------------------------------------------

function fmtApr(v: number | null | undefined): string {
  if (v == null) return "—";
  return (v * 100).toFixed(2) + "%";
}

function fmtUsd(v: number | null | undefined): string {
  if (v == null) return "—";
  if (Math.abs(v) >= 1e9) return `$${(v / 1e9).toFixed(2)}B`;
  if (Math.abs(v) >= 1e6) return `$${(v / 1e6).toFixed(1)}M`;
  return `$${v.toLocaleString(undefined, { maximumFractionDigits: 0 })}`;
}

function fmtCoin(v: number | null | undefined, sym: string): string {
  if (v == null) return "—";
  if (Math.abs(v) >= 1e6) return `${(v / 1e6).toFixed(2)}M ${sym}`;
  if (Math.abs(v) >= 1e3) return `${(v / 1e3).toFixed(1)}K ${sym}`;
  return `${v.toFixed(2)} ${sym}`;
}

function aprColor(v: number | null): string {
  if (v == null) return "var(--text-muted)";
  if (v > 0.005) return "var(--green)";
  if (v < -0.005) return "var(--red)";
  return "var(--text-secondary)";
}

// ---------------------------------------------------------------------------
// Distribution helpers (browser-side Gaussian KDE + histogram)
// ---------------------------------------------------------------------------

function buildHistogram(values: number[], bins: number): { x: number; count: number }[] {
  if (!values.length) return [];
  const min = Math.min(...values);
  const max = Math.max(...values);
  const width = (max - min) / bins || 1;
  const counts = Array(bins).fill(0);
  values.forEach((v) => {
    const i = Math.min(Math.floor((v - min) / width), bins - 1);
    counts[i]++;
  });
  return counts.map((c, i) => ({ x: min + (i + 0.5) * width, count: c }));
}

function gaussianKDE(values: number[], bw: number) {
  const n = values.length;
  return (x: number) =>
    values.reduce((acc, xi) => acc + Math.exp(-0.5 * ((x - xi) / bw) ** 2), 0) /
    (n * bw * Math.sqrt(2 * Math.PI));
}

/** Linear-interpolation empirical quantile (matches pandas .quantile default). */
function percentileOf(sorted: number[], p: number): number {
  if (!sorted.length) return 0;
  const idx = (p / 100) * (sorted.length - 1);
  const lo = Math.floor(idx);
  const hi = Math.ceil(idx);
  return sorted[lo] + (sorted[hi] - sorted[lo]) * (idx - lo);
}

function stdDev(vals: number[]): number {
  if (vals.length < 2) return 0;
  const mean = vals.reduce((a, b) => a + b, 0) / vals.length;
  return Math.sqrt(vals.reduce((a, b) => a + (b - mean) ** 2, 0) / (vals.length - 1));
}

/**
 * KDE-CDF percentile — matches the analytics_frontend reference implementation:
 *   1. Evaluate Gaussian KDE at 200 points over [min−2σ, max+2σ]
 *   2. Normalised cumsum → CDF
 *   3. Binary-search for the p-th percentile value
 */
function kdePercentile(vals: number[], bw: number, p: number): number {
  if (!vals.length) return 0;
  const std = stdDev(vals);
  const lo = Math.min(...vals) - 2 * std;
  const hi = Math.max(...vals) + 2 * std;
  const N = 200;
  const xs = Array.from({ length: N }, (_, i) => lo + (i / (N - 1)) * (hi - lo));
  const kde = gaussianKDE(vals, bw);
  const ys = xs.map(kde);
  const total = ys.reduce((a, b) => a + b, 0);
  let cum = 0;
  const target = p / 100;
  for (let i = 0; i < N; i++) {
    cum += ys[i] / total;
    if (cum >= target) return xs[i];
  }
  return xs[N - 1];
}

// ---------------------------------------------------------------------------
// Instruments reference panel
// ---------------------------------------------------------------------------

const INSTRUMENT_MAP: Array<{
  exchange: string;
  label: string;
  instrument: string;
  settlement: string;
  note?: string;
}> = [
  { exchange: "binance", label: "Binance",  instrument: "BTCUSDT (linear perp)",       settlement: "USDT" },
  { exchange: "okx",     label: "OKX",      instrument: "BTC-USDT-SWAP",               settlement: "USDT" },
  { exchange: "bybit",   label: "Bybit",    instrument: "BTCUSDT (linear category)",   settlement: "USDT" },
  { exchange: "deribit", label: "Deribit",  instrument: "BTC-PERPETUAL",               settlement: "USD",  note: "Inverse/coin-margined" },
  { exchange: "bullish", label: "Bullish",  instrument: "BTC (native perp)",           settlement: "USDT" },
];

function InstrumentPanel({ symbol }: { symbol: string }) {
  return (
    <div className="card" style={{ marginBottom: 0 }}>
      <div className="card-header">Instruments &amp; Pairs</div>
      <table className="fn-table" style={{ fontSize: 11 }}>
        <thead>
          <tr>
            <th>Exchange</th>
            <th>Instrument</th>
            <th>Settlement</th>
            <th>Annualisation</th>
            <th>Notes</th>
          </tr>
        </thead>
        <tbody>
          {INSTRUMENT_MAP.map(({ exchange, label, instrument, settlement, note }) => {
            const sym = symbol.toUpperCase();
            const inst = instrument.replace("BTC", sym);
            return (
              <tr key={exchange}>
                <td className="fn-exch-name">
                  <span className="fn-dot" style={{ background: EXCHANGE_COLORS[exchange] }} />
                  {label}
                </td>
                <td style={{ fontFamily: "var(--font-mono)" }}>{inst}</td>
                <td>
                  <span style={{ color: settlement === "USD" ? "var(--yellow)" : "var(--green)", fontFamily: "var(--font-mono)" }}>
                    {settlement}
                  </span>
                </td>
                <td style={{ fontFamily: "var(--font-mono)", color: "var(--text-muted)" }}>
                  rate × 3 × 365
                </td>
                <td style={{ color: "var(--text-muted)" }}>{note ?? "—"}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function AprCell({ value }: { value: number | null }) {
  return (
    <td style={{ color: aprColor(value), fontFamily: "var(--font-mono)", fontSize: 12 }}>
      {fmtApr(value)}
    </td>
  );
}

function SnapshotTable({
  snap,
  symbol,
}: {
  snap: FundingSnapshotResp;
  symbol: string;
}) {
  const rows: Array<{
    key: string;
    label: string;
    data: Partial<ExchangeSnap>;
    isDeribit?: boolean;
  }> = EXCHANGES.map((e) => ({
    key: e,
    label: EXCHANGE_LABELS[e],
    data: snap.exchanges[e] ?? {},
    isDeribit: e === "deribit",
  }));

  const { equal_weighted_apr, oi_weighted_apr, volume_weighted_apr } = snap.blended;

  return (
    <div className="card" style={{ overflowX: "auto" }}>
      <div className="card-header">Exchange Funding Snapshot</div>
      <table className="fn-table">
        <thead>
          <tr>
            <th>Exchange</th>
            <th>Live APR</th>
            <th>Last APR</th>
            <th>7d MA APR</th>
            <th>30d MA APR</th>
            <th>OI (USD)</th>
            <th>OI (Coin)</th>
            <th>Interval (h)</th>
            <th>Vol 24h (USD)</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(({ key, label, data, isDeribit }) => (
            <tr key={key} title={isDeribit ? "USD-settled (not USDT)" : undefined}>
              <td className="fn-exch-name">
                <span
                  className="fn-dot"
                  style={{ background: EXCHANGE_COLORS[key] }}
                />
                {label}
                {isDeribit && (
                  <span className="fn-badge" title="USD-settled">USD</span>
                )}
              </td>
              <AprCell value={data.live_apr ?? null} />
              <AprCell value={data.last_apr ?? null} />
              <AprCell value={data.ma_7d_apr ?? null} />
              <AprCell value={data.ma_30d_apr ?? null} />
              <td>{fmtUsd(data.oi_usd)}</td>
              <td>{fmtCoin(data.oi_coin, symbol)}</td>
              <td>{data.funding_interval_hours ?? "—"}</td>
              <td>{data.volume_usd_24h != null ? fmtUsd(data.volume_usd_24h) : fmtCoin(data.volume_coin_24h, symbol)}</td>
            </tr>
          ))}

          {/* Blended sub-rows */}
          {[
            { label: "Blended (equal)", value: equal_weighted_apr },
            { label: "Blended (OI-wt)", value: oi_weighted_apr },
            { label: "Blended (vol-wt)", value: volume_weighted_apr },
          ].map(({ label, value }) => (
            <tr key={label} className="fn-blend-row">
              <td className="fn-exch-name" style={{ color: "var(--text-muted)", paddingLeft: "1.5rem" }}>
                {label}
              </td>
              <AprCell value={value} />
              <td colSpan={7} />
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Controls({
  symbol,
  setSymbol,
  selectedExchanges,
  toggleExchange,
  blendMode,
  setBlendMode,
  days,
  setDays,
  maPeriods,
  setMaPeriods,
  histBins,
  setHistBins,
  hideSymbolPicker = false,
}: {
  symbol: string;
  setSymbol: (s: string) => void;
  selectedExchanges: Set<string>;
  toggleExchange: (e: string) => void;
  blendMode: string;
  setBlendMode: (m: string) => void;
  days: number;
  setDays: (d: number) => void;
  maPeriods: [number, number, number];
  setMaPeriods: (p: [number, number, number]) => void;
  histBins: number;
  setHistBins: (b: number) => void;
  hideSymbolPicker?: boolean;
}) {
  return (
    <div className="card fn-controls">
      {!hideSymbolPicker && (
        <div className="fn-control-group">
          <span className="fn-control-label">SYMBOL</span>
          {SYMBOLS.map((s) => (
            <button
              key={s}
              className={`fn-chip${symbol === s ? " fn-chip--active" : ""}`}
              onClick={() => setSymbol(s)}
            >
              {s}
            </button>
          ))}
        </div>
      )}

      <div className="fn-control-group">
        <span className="fn-control-label">EXCHANGES</span>
        {EXCHANGES.map((e) => (
          <label key={e} className="fn-checkbox-label">
            <input
              type="checkbox"
              checked={selectedExchanges.has(e)}
              onChange={() => toggleExchange(e)}
            />
            <span
              className="fn-dot"
              style={{ background: EXCHANGE_COLORS[e], width: 8, height: 8 }}
            />
            {EXCHANGE_LABELS[e]}
          </label>
        ))}
      </div>

      <div className="fn-control-group">
        <span className="fn-control-label">BLEND</span>
        {["off", "equal", "oi", "volume"].map((m) => (
          <button
            key={m}
            className={`fn-chip${blendMode === m ? " fn-chip--active" : ""}`}
            onClick={() => setBlendMode(m)}
          >
            {m === "off" ? "Off" : m === "equal" ? "Equal" : m === "oi" ? "OI-wt" : "Vol-wt"}
          </button>
        ))}
      </div>

      <div className="fn-control-group">
        <span className="fn-control-label">DAYS</span>
        {[30, 90, 180, 365].map((d) => (
          <button
            key={d}
            className={`fn-chip${days === d ? " fn-chip--active" : ""}`}
            onClick={() => setDays(d)}
          >
            {d}d
          </button>
        ))}
        <input
          type="number"
          className="fn-ma-input"
          value={days}
          min={7}
          max={730}
          title="Custom day count (7–730)"
          onChange={(e) => {
            const v = parseInt(e.target.value);
            if (v >= 7 && v <= 730) setDays(v);
          }}
          style={{ width: 54 }}
        />
      </div>

      <div className="fn-control-group">
        <span className="fn-control-label">MA PERIODS</span>
        {[0, 1, 2].map((i) => (
          <input
            key={i}
            type="number"
            className="fn-ma-input"
            value={maPeriods[i]}
            min={1}
            max={365}
            onChange={(e) => {
              const next = [...maPeriods] as [number, number, number];
              next[i] = parseInt(e.target.value) || maPeriods[i];
              setMaPeriods(next);
            }}
          />
        ))}
        <span style={{ fontSize: 11, color: "var(--text-muted)" }}>days</span>
      </div>

      <div className="fn-control-group">
        <span className="fn-control-label">HIST BINS</span>
        {[10, 20, 30, 50, 100].map((b) => (
          <button
            key={b}
            className={`fn-chip${histBins === b ? " fn-chip--active" : ""}`}
            onClick={() => setHistBins(b)}
          >
            {b}
          </button>
        ))}
        <input
          type="number"
          className="fn-ma-input"
          value={histBins}
          min={5}
          max={200}
          title="Custom bin count (5–200)"
          onChange={(e) => {
            const v = parseInt(e.target.value);
            if (v >= 5 && v <= 200) setHistBins(v);
          }}
          style={{ width: 54 }}
        />
      </div>
    </div>
  );
}

function FundingChart({
  symbol,
  selectedExchanges,
  blendMode,
  days,
  maPeriods,
}: {
  symbol: string;
  selectedExchanges: Set<string>;
  blendMode: string;
  days: number;
  maPeriods: [number, number, number];
}) {
  const [histories, setHistories] = useState<Record<string, SeriesPoint[]>>({});
  const [blendedSeries, setBlendedSeries] = useState<Record<string, SeriesPoint[]>>({});
  const [loading, setLoading] = useState(false);

  // Warmup = longest MA period so the MA window is fully primed at t=0
  const warmup = Math.max(...maPeriods);

  useEffect(() => {
    setLoading(true);
    const isBlend = blendMode !== "off";

    const fetchAll = async () => {
      if (isBlend) {
        const data = await fetchHistory(symbol, "binance", days, true, warmup);
        if (data?.blend_series) {
          setBlendedSeries({
            equal: data.blend_series.equal_weighted,
            oi: data.blend_series.oi_weighted,
            volume: data.blend_series.volume_weighted,
          });
        }
        const individual = await Promise.all(
          [...selectedExchanges].map((e) => fetchHistory(symbol, e, days, false, warmup))
        );
        const map: Record<string, SeriesPoint[]> = {};
        [...selectedExchanges].forEach((e, i) => {
          if (individual[i]?.series) map[e] = individual[i]!.series;
        });
        setHistories(map);
      } else {
        const results = await Promise.all(
          [...selectedExchanges].map((e) => fetchHistory(symbol, e, days, false, warmup))
        );
        const map: Record<string, SeriesPoint[]> = {};
        [...selectedExchanges].forEach((e, i) => {
          if (results[i]?.series) map[e] = results[i]!.series;
        });
        setHistories(map);
      }
      setLoading(false);
    };

    fetchAll();
  }, [symbol, days, blendMode, selectedExchanges, warmup]);

  const option = useMemo(() => {
    const isBlend = blendMode !== "off";
    const series: object[] = [];
    const displayCutoffDate = new Date(Date.now() - days * 86_400_000).toISOString().slice(0, 10);

    // Individual exchange lines — trim warmup prefix from display
    [...selectedExchanges].forEach((e) => {
      const pts = histories[e];
      if (!pts?.length) return;
      series.push({
        name: EXCHANGE_LABELS[e],
        type: "line",
        data: pts
          .filter((p) => p.date >= displayCutoffDate)
          .map((p) => [p.date, (p.value * 100).toFixed(4)]),
        symbol: "none",
        lineStyle: {
          color: EXCHANGE_COLORS[e],
          width: isBlend ? 1 : 1.5,
          opacity: isBlend ? 0.35 : 0.9,
        },
        itemStyle: { color: EXCHANGE_COLORS[e] },
        smooth: true,
      });
    });

    // Blended overlay
    if (isBlend) {
      const blendKey = blendMode === "equal" ? "equal" : blendMode === "oi" ? "oi" : "volume";
      const blendColor =
        blendKey === "equal"
          ? EXCHANGE_COLORS.blend_equal
          : blendKey === "oi"
          ? EXCHANGE_COLORS.blend_oi
          : EXCHANGE_COLORS.blend_vol;
      const blendLabel =
        blendKey === "equal" ? "Blended (equal)" : blendKey === "oi" ? "Blended (OI)" : "Blended (vol)";
      const pts = blendedSeries[blendKey];
      if (pts?.length) {
        series.push({
          name: blendLabel,
          type: "line",
          data: pts
            .filter((p) => p.date >= displayCutoffDate)
            .map((p) => [p.date, (p.value * 100).toFixed(4)]),
          symbol: "none",
          lineStyle: { color: blendColor, width: 2.5 },
          itemStyle: { color: blendColor },
          z: 10,
          smooth: false,
        });
      }
    }

    // MA overlays: only shown when there is a single unambiguous source series —
    // either exactly one exchange is selected, or blend mode is active (which
    // collapses all exchanges into one line).  With multiple raw exchange lines
    // visible simultaneously the overlays create visual noise with no clear
    // attachment point.
    const showMAs = isBlend || selectedExchanges.size === 1;
    const maSource = showMAs
      ? (isBlend && blendedSeries[(blendMode === "equal" ? "equal" : blendMode === "oi" ? "oi" : "volume")]
          ? blendedSeries[blendMode === "equal" ? "equal" : blendMode === "oi" ? "oi" : "volume"]
          : histories[[...selectedExchanges][0]])
      : null;
    const MA_COLORS = ["#f59e0b88", "#ef444488", "#22c55e88"];

    if (maSource?.length) {
      // Display cutoff: exclude the warmup prefix
      const displayCutoff = new Date(Date.now() - days * 86_400_000).toISOString().slice(0, 10);

      maPeriods.forEach((period, i) => {
        const values = maSource.map((p) => p.value * 100);
        const maVals = values.map((_, j) => {
          if (j < period - 1) return null;
          const w = values.slice(j - period + 1, j + 1);
          return w.reduce((a, b) => a + b, 0) / w.length;
        });
        series.push({
          name: `MA${period}`,
          type: "line",
          data: maSource
            .map((p, j) => [p.date, maVals[j]?.toFixed(4) ?? null] as [string, string | null])
            .filter(([date]) => date >= displayCutoff),
          symbol: "none",
          lineStyle: { color: MA_COLORS[i], width: 1, type: "dashed" },
          itemStyle: { color: MA_COLORS[i] },
        });
      });
    }

    return {
      backgroundColor: "transparent",
      grid: { top: 40, right: 20, bottom: 60, left: 60, containLabel: false },
      tooltip: {
        trigger: "axis",
        backgroundColor: "var(--surface-2)",
        borderColor: "var(--border)",
        textStyle: { color: "var(--text-primary)", fontSize: 11, fontFamily: "var(--font-mono)" },
        formatter: (params: { seriesName: string; value: [string, string] }[]) => {
          const date = params[0]?.value?.[0] ?? "";
          const lines = params
            .map((p) => `<span style="color:${EXCHANGE_COLORS[p.seriesName.toLowerCase().split(" ")[0]] ?? "#aaa"}">${p.seriesName}</span>: ${p.value?.[1] ?? "—"}%`)
            .join("<br/>");
          return `<div style="padding:4px 6px"><b>${date}</b><br/>${lines}</div>`;
        },
      },
      legend: {
        top: 8,
        textStyle: { color: "var(--text-secondary)", fontSize: 11 },
        inactiveColor: "var(--text-muted)",
      },
      xAxis: {
        type: "time",
        axisLine: { lineStyle: { color: "var(--border)" } },
        axisLabel: { color: "var(--text-muted)", fontSize: 10, fontFamily: "var(--font-mono)" },
        splitLine: { show: false },
      },
      yAxis: {
        type: "value",
        axisLabel: {
          color: "var(--text-muted)",
          fontSize: 10,
          fontFamily: "var(--font-mono)",
          formatter: (v: number) => `${v.toFixed(1)}%`,
        },
        splitLine: { lineStyle: { color: "var(--border-subtle)", type: "dashed" } },
        axisLine: { show: false },
      },
      series,
    };
  }, [histories, blendedSeries, selectedExchanges, blendMode, maPeriods]);

  return (
    <div className="card">
      <div className="card-header">Funding Rate History</div>
      {loading && <div className="fn-loading">Loading…</div>}
      <ReactECharts
        option={option}
        style={{ height: 320 }}
        theme="dark"
        notMerge
      />
    </div>
  );
}

function DistributionPanel({
  symbol,
  selectedExchanges,
  days,
  maPeriods,
  kdeBw,
  setKdeBw,
  histBins,
}: {
  symbol: string;
  selectedExchanges: Set<string>;
  days: number;
  maPeriods: [number, number, number];
  kdeBw: number;
  setKdeBw: (v: number) => void;
  histBins: number;
}) {
  const exchList = [...selectedExchanges];

  // Which exchange the distribution is currently showing
  const [distExchange, setDistExchange] = useState<string>(exchList[0] ?? "binance");

  // Reset to first exchange whenever the selection changes
  useEffect(() => {
    const list = [...selectedExchanges];
    if (!list.includes(distExchange)) setDistExchange(list[0] ?? "binance");
  }, [selectedExchanges]); // eslint-disable-line react-hooks/exhaustive-deps

  const [histData, setHistData] = useState<Record<string, number[]>>({});
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    const load = async () => {
      setLoading(true);
      const maxPeriod = Math.max(...maPeriods);
      const warmup = maxPeriod * 2 + 1;
      const data = await fetchHistory(symbol, distExchange, days, false, warmup);
      setLoading(false);
      if (!data?.series?.length) { setHistData({}); return; }

      const fullVals = data.series.map((p) => p.value * 100);
      const displayCutoff = new Date(Date.now() - days * 86_400_000).toISOString().slice(0, 10);

      const maData: Record<string, number[]> = {};
      maPeriods.forEach((period) => {
        const trimmed: number[] = [];
        for (let j = period - 1; j < fullVals.length; j++) {
          if (data.series[j].date < displayCutoff) continue;
          const w = fullVals.slice(j - period + 1, j + 1);
          trimmed.push(w.reduce((a, b) => a + b, 0) / w.length);
        }
        maData[`MA${period}`] = trimmed;
      });
      setHistData(maData);
    };
    load();
  }, [symbol, distExchange, days, maPeriods]);

  const [pctTarget, setPctTarget] = useState(50);

  // Derived: per-MA percentile values at pctTarget (empirical)
  const pctValues = useMemo(() =>
    Object.fromEntries(
      Object.entries(histData).map(([label, vals]) => {
        const s = [...vals].sort((a, b) => a - b);
        return [label, percentileOf(s, pctTarget)];
      })
    ),
  [histData, pctTarget]);

  const MA_COLORS = ["#f59e0b", "#ef4444", "#22c55e"];

  const histogramOption = useMemo(() => {
    const series: object[] = [];
    const allVals = Object.values(histData).flat();
    if (!allVals.length) return {};

    const globalMin = Math.min(...allVals);
    const globalMax = Math.max(...allVals);
    const kdeXs = Array.from({ length: 200 }, (_, i) => globalMin + (i / 199) * (globalMax - globalMin));

    Object.entries(histData).forEach(([label, vals], idx) => {
      const hist = buildHistogram(vals, histBins);
      const kde = gaussianKDE(vals, kdeBw);
      const kdeSeries = kdeXs.map((x) => [x, kde(x) * vals.length * ((globalMax - globalMin) / histBins)]);
      const pctValue = pctValues[label] ?? 0;

      series.push({
        name: label,
        type: "bar",
        data: hist.map((b) => [b.x, b.count]),
        itemStyle: { color: MA_COLORS[idx] + "55" },
        barWidth: "60%",
      });
      series.push({
        name: `${label} KDE`,
        type: "line",
        data: kdeSeries,
        lineStyle: { color: MA_COLORS[idx], width: 1.5 },
        symbol: "none",
        smooth: true,
      });
      series.push({
        name: `${label} P${pctTarget}`,
        type: "line",
        data: [[pctValue, 0], [pctValue, Math.max(...hist.map((b) => b.count)) * 1.1]],
        lineStyle: { color: MA_COLORS[idx], width: 1, type: "dashed" },
        symbol: "none",
      });
    });

    return {
      backgroundColor: "transparent",
      grid: { top: 30, right: 20, bottom: 40, left: 50, containLabel: false },
      tooltip: {
        trigger: "axis",
        backgroundColor: "var(--surface-2)",
        borderColor: "var(--border)",
        textStyle: { color: "var(--text-primary)", fontSize: 11 },
      },
      legend: { top: 4, textStyle: { color: "var(--text-secondary)", fontSize: 10 } },
      xAxis: {
        type: "value",
        axisLabel: { color: "var(--text-muted)", fontSize: 10, formatter: (v: number) => `${v.toFixed(1)}%` },
        splitLine: { show: false },
      },
      yAxis: {
        type: "value",
        axisLabel: { color: "var(--text-muted)", fontSize: 10 },
        splitLine: { lineStyle: { color: "var(--border-subtle)", type: "dashed" } },
      },
      series,
    };
  }, [histData, kdeBw, pctTarget, histBins, pctValues]);

  const boxOption = useMemo(() => {
    if (!Object.keys(histData).length) return {};
    const boxData = Object.entries(histData).map(([label, vals]) => {
      const s = [...vals].sort((a, b) => a - b);
      return {
        name: label,
        value: [s[0], percentileOf(s, 25), percentileOf(s, 50), percentileOf(s, 75), s[s.length - 1]],
      };
    });
    return {
      backgroundColor: "transparent",
      grid: { top: 20, right: 20, bottom: 30, left: 70 },
      tooltip: {
        trigger: "item",
        backgroundColor: "var(--surface-2)",
        borderColor: "var(--border)",
        textStyle: { color: "var(--text-primary)", fontSize: 11 },
      },
      xAxis: {
        type: "value",
        axisLabel: { color: "var(--text-muted)", fontSize: 10, formatter: (v: number) => `${v.toFixed(1)}%` },
        splitLine: { lineStyle: { color: "var(--border-subtle)", type: "dashed" } },
      },
      yAxis: {
        type: "category",
        data: boxData.map((b) => b.name),
        axisLabel: { color: "var(--text-secondary)", fontSize: 11 },
        axisLine: { lineStyle: { color: "var(--border)" } },
      },
      series: boxData.map((b, i) => ({
        name: b.name,
        type: "boxplot",
        data: [b.value],
        itemStyle: { color: MA_COLORS[i] + "55", borderColor: MA_COLORS[i] },
      })),
    };
  }, [histData]);

  const hasData = Object.keys(histData).length > 0;

  return (
    <div className="card">
      <div className="card-header">Distribution Analysis</div>

      {/* Exchange selector */}
      <div className="fn-control-group" style={{ marginBottom: "1rem" }}>
        <span className="fn-control-label">EXCHANGE</span>
        {exchList.map((e) => (
          <button
            key={e}
            className={`fn-chip${distExchange === e ? " fn-chip--active" : ""}`}
            onClick={() => setDistExchange(e)}
          >
            <span
              className="fn-dot"
              style={{ background: EXCHANGE_COLORS[e], width: 7, height: 7, marginRight: 4 }}
            />
            {EXCHANGE_LABELS[e]}
          </button>
        ))}
        {loading && (
          <span style={{ fontSize: 11, color: "var(--text-muted)", marginLeft: 12 }}>Loading…</span>
        )}
      </div>

      {hasData && (
        <>
          {/* ── Large percentile headline cards ── */}
          <div style={{
            display: "grid",
            gridTemplateColumns: `repeat(${maPeriods.length}, 1fr)`,
            gap: "0.75rem",
            marginBottom: "1.25rem",
          }}>
            {maPeriods.map((period, i) => {
              const label = `MA${period}`;
              const val = pctValues[label];
              const color = val == null ? "var(--text-muted)"
                : val > 0.05 ? "var(--green)"
                : val < -0.05 ? "var(--red)"
                : "var(--text-secondary)";
              return (
                <div key={label} style={{
                  background: "var(--surface-2)",
                  border: "1px solid var(--border)",
                  borderRadius: 6,
                  padding: "0.75rem 1rem",
                  textAlign: "center",
                }}>
                  <div style={{
                    fontSize: 11,
                    color: "var(--text-muted)",
                    letterSpacing: "0.08em",
                    marginBottom: 4,
                  }}>
                    {label} · P{pctTarget}
                    <span style={{ marginLeft: 6, color: MA_COLORS[i], fontSize: 10 }}>●</span>
                  </div>
                  <div style={{
                    fontSize: 28,
                    fontWeight: 700,
                    fontFamily: "var(--font-mono)",
                    color,
                    lineHeight: 1.1,
                  }}>
                    {val != null ? `${val.toFixed(2)}%` : "—"}
                  </div>
                  <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 3 }}>
                    {EXCHANGE_LABELS[distExchange]} · {days}d window
                  </div>
                </div>
              );
            })}
          </div>

          {/* Controls row */}
          <div className="fn-dist-controls">
            <label className="fn-control-label">
              KDE BW
              <input
                type="range" min={0.05} max={1.5} step={0.05} value={kdeBw}
                onChange={(e) => setKdeBw(parseFloat(e.target.value))}
                style={{ marginLeft: 8, width: 100 }}
              />
              <span style={{ fontFamily: "var(--font-mono)", fontSize: 11 }}>{kdeBw.toFixed(2)}</span>
            </label>

            <label className="fn-control-label" style={{ marginLeft: 24 }}>
              Percentile
              <input
                type="range" min={1} max={99} value={pctTarget}
                onChange={(e) => setPctTarget(parseInt(e.target.value))}
                style={{ marginLeft: 8, width: 100 }}
              />
              <span style={{ fontFamily: "var(--font-mono)", fontSize: 11 }}>P{pctTarget}</span>
            </label>
          </div>

          {/* Histogram + Box plot */}
          <div style={{ display: "grid", gridTemplateColumns: "2fr 1fr", gap: "1rem" }}>
            <ReactECharts option={histogramOption} style={{ height: 220 }} theme="dark" notMerge />
            <ReactECharts option={boxOption} style={{ height: 220 }} theme="dark" notMerge />
          </div>

          {/* KDE Distribution Metrics */}
          <div style={{ marginTop: "1rem" }}>
            <div style={{ fontSize: 11, fontWeight: 600, color: "var(--text-secondary)", marginBottom: 4, letterSpacing: "0.05em" }}>
              KDE DISTRIBUTION METRICS
              <span style={{ fontWeight: 400, color: "var(--text-muted)", marginLeft: 8 }}>
                (CDF of Gaussian KDE at 200pts over [μ±2σ], bw={kdeBw.toFixed(2)})
              </span>
            </div>
            <table className="fn-table fn-pct-table">
              <thead>
                <tr>
                  <th>Series</th><th>N pts</th><th>P5</th><th>P25</th><th>P50</th><th>P75</th><th>P95</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(histData).map(([label, vals]) => (
                  <tr key={label}>
                    <td>{label}</td>
                    <td style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--text-muted)" }}>{vals.length}</td>
                    {[5, 25, 50, 75, 95].map((p) => (
                      <td key={p} style={{ fontFamily: "var(--font-mono)", fontSize: 11 }}>
                        {kdePercentile(vals, kdeBw, p).toFixed(2)}%
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* Empirical Distribution Metrics */}
          <div style={{ marginTop: "1rem" }}>
            <div style={{ fontSize: 11, fontWeight: 600, color: "var(--text-secondary)", marginBottom: 4, letterSpacing: "0.05em" }}>
              EMPIRICAL DISTRIBUTION METRICS
              <span style={{ fontWeight: 400, color: "var(--text-muted)", marginLeft: 8 }}>
                (linear interpolation quantiles on raw MA values)
              </span>
            </div>
            <table className="fn-table fn-pct-table">
              <thead>
                <tr>
                  <th>Series</th><th>N pts</th><th>P5</th><th>P25</th><th>P50 (Median)</th><th>P75</th><th>P95</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(histData).map(([label, vals]) => {
                  const s = [...vals].sort((a, b) => a - b);
                  return (
                    <tr key={label}>
                      <td>{label}</td>
                      <td style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--text-muted)" }}>{vals.length}</td>
                      {[5, 25, 50, 75, 95].map((p) => (
                        <td key={p} style={{ fontFamily: "var(--font-mono)", fontSize: 11 }}>
                          {percentileOf(s, p).toFixed(2)}%
                        </td>
                      ))}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </>
      )}

      {!hasData && !loading && (
        <div style={{ color: "var(--text-muted)", fontSize: 12, padding: "1rem 0" }}>
          No data — select an exchange above or adjust lookback.
        </div>
      )}
    </div>
  );
}

function CoinglassStrip({
  coinglass,
  internalSnap,
  asOf,
}: {
  coinglass: FundingSnapshotResp["coinglass"];
  internalSnap: FundingSnapshotResp["exchanges"];
  asOf: string;
}) {
  const entries: Array<{ key: keyof typeof coinglass; label: string; exch: string }> = [
    { key: "binance_apr", label: "Binance", exch: "binance" },
    { key: "okx_apr", label: "OKX", exch: "okx" },
    { key: "bybit_apr", label: "Bybit", exch: "bybit" },
  ];

  const hasData = entries.some((e) => coinglass[e.key] != null);
  if (!hasData) {
    return (
      <div className="card fn-cg-strip">
        <span className="fn-cg-label">Coinglass</span>
        <span style={{ color: "var(--text-muted)", fontSize: 11 }}>— no data (API key not set)</span>
      </div>
    );
  }

  return (
    <div className="card fn-cg-strip">
      <span className="fn-cg-label">Coinglass cross-check</span>
      {entries.map(({ key, label, exch }) => {
        const cgVal = coinglass[key];
        const internal = internalSnap[exch]?.live_apr ?? null;
        const delta = cgVal != null && internal != null ? cgVal - internal : null;
        return (
          <div key={key} className="fn-cg-badge" title={`Internal: ${fmtApr(internal)}`}>
            <span style={{ color: EXCHANGE_COLORS[exch] }}>{label}</span>
            <span style={{ color: aprColor(cgVal), fontFamily: "var(--font-mono)" }}>
              {fmtApr(cgVal)}
            </span>
            {delta != null && (
              <span
                style={{
                  fontSize: 10,
                  color: Math.abs(delta) > 0.01 ? "var(--yellow)" : "var(--text-muted)",
                  fontFamily: "var(--font-mono)",
                }}
              >
                Δ{fmtApr(delta)}
              </span>
            )}
          </div>
        );
      })}
      <span className="fn-cg-ts">
        Source: Coinglass &nbsp;·&nbsp; {new Date(asOf).toLocaleTimeString()}
      </span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Embeddable dashboard component
// ---------------------------------------------------------------------------

export interface FundingDashboardProps {
  /**
   * Symbol pre-selected when the dashboard mounts.
   * Defaults to "BTC".
   */
  initialSymbol?: string;
  /**
   * When false the SYMBOL chip row is hidden — useful when the symbol is
   * already determined by the parent context (e.g. an asset page).
   */
  showSymbolPicker?: boolean;
}

export function FundingDashboard({
  initialSymbol = "BTC",
  showSymbolPicker = true,
}: FundingDashboardProps) {
  const [symbol, setSymbol] = useState(initialSymbol.toUpperCase());
  const [selectedExchanges, setSelectedExchanges] = useState<Set<Exchange>>(
    new Set(["binance", "okx", "bybit", "deribit"])
  );
  const [blendMode, setBlendMode] = useState("off");
  const [days, setDays] = useState(90);
  const [maPeriods, setMaPeriods] = useState<[number, number, number]>([7, 30, 90]);
  const [kdeBw, setKdeBw] = useState(0.4);
  const [histBins, setHistBins] = useState(30);
  const [snap, setSnap] = useState<FundingSnapshotResp | null>(null);
  const [snapLoading, setSnapLoading] = useState(true);

  const toggleExchange = useCallback((e: string) => {
    setSelectedExchanges((prev) => {
      const next = new Set(prev);
      if (next.has(e as Exchange)) {
        if (next.size > 1) next.delete(e as Exchange);
      } else {
        next.add(e as Exchange);
      }
      return next;
    });
  }, []);

  useEffect(() => {
    setSnapLoading(true);
    fetchSnapshot(symbol).then((data) => {
      setSnap(data);
      setSnapLoading(false);
    });
  }, [symbol]);

  return (
    <div className="fn-page">
      {/* Section 1: Snapshot table */}
      {snapLoading ? (
        <div className="card fn-loading">Loading snapshot…</div>
      ) : snap ? (
        <SnapshotTable snap={snap} symbol={symbol} />
      ) : (
        <div className="card fn-empty">No funding data for {symbol} — check ingestion.</div>
      )}

      {/* Instruments reference */}
      <InstrumentPanel symbol={symbol} />

      {/* Section 2: Controls (symbol picker optionally hidden) */}
      <Controls
        symbol={symbol}
        setSymbol={showSymbolPicker ? setSymbol : () => {}}
        selectedExchanges={selectedExchanges}
        toggleExchange={toggleExchange}
        blendMode={blendMode}
        setBlendMode={setBlendMode}
        days={days}
        setDays={setDays}
        maPeriods={maPeriods}
        setMaPeriods={setMaPeriods}
        histBins={histBins}
        setHistBins={setHistBins}
        hideSymbolPicker={!showSymbolPicker}
      />

      {/* Section 3: Chart */}
      <FundingChart
        symbol={symbol}
        selectedExchanges={selectedExchanges}
        blendMode={blendMode}
        days={days}
        maPeriods={maPeriods}
      />

      {/* Section 4: Distribution */}
      <DistributionPanel
        symbol={symbol}
        selectedExchanges={selectedExchanges}
        days={days}
        maPeriods={maPeriods}
        kdeBw={kdeBw}
        setKdeBw={setKdeBw}
        histBins={histBins}
      />

      {/* Section 5: Coinglass strip */}
      {snap && (
        <CoinglassStrip
          coinglass={snap.coinglass}
          internalSnap={snap.exchanges}
          asOf={snap.as_of}
        />
      )}
    </div>
  );
}

/** @deprecated Use FundingDashboard directly. Kept for page.tsx backward compat. */
export default function FundingPage() {
  return <FundingDashboard initialSymbol="BTC" showSymbolPicker />;
}
