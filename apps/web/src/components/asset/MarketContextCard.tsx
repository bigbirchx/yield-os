"use client";

/**
 * MarketContextCard
 *
 * Displays CoinGecko market reference data for one asset:
 *   - current price, 24h change, market cap, 24h volume
 *   - circulating supply vs total/max supply
 *   - sparkline price history (ECharts)
 *
 * This is CONTEXT only — it does not replace protocol-native lending
 * rates, LTVs, or derivatives routing.
 */

import dynamic from "next/dynamic";
import type { AssetDetail, AssetMarketHistory } from "@/types/api";

const ReactECharts = dynamic(() => import("echarts-for-react"), { ssr: false });

// ---------------------------------------------------------------------------
// Formatters
// ---------------------------------------------------------------------------

function fmtUsd(v: number | null | undefined, compact = false): string {
  if (v == null) return "—";
  if (compact) {
    if (Math.abs(v) >= 1e12) return `$${(v / 1e12).toFixed(2)}T`;
    if (Math.abs(v) >= 1e9) return `$${(v / 1e9).toFixed(2)}B`;
    if (Math.abs(v) >= 1e6) return `$${(v / 1e6).toFixed(1)}M`;
    return `$${v.toLocaleString(undefined, { maximumFractionDigits: 0 })}`;
  }
  return `$${v.toLocaleString(undefined, { maximumFractionDigits: 2 })}`;
}

function fmtPct(v: number | null | undefined): string {
  if (v == null) return "—";
  return `${v >= 0 ? "+" : ""}${v.toFixed(2)}%`;
}

function fmtSupply(v: number | null | undefined): string {
  if (v == null) return "—";
  if (v >= 1e9) return `${(v / 1e9).toFixed(2)}B`;
  if (v >= 1e6) return `${(v / 1e6).toFixed(2)}M`;
  return v.toLocaleString(undefined, { maximumFractionDigits: 0 });
}

function changeColor(v: number | null): string {
  if (v == null) return "var(--text-muted)";
  return v >= 0 ? "var(--green)" : "var(--red)";
}

// ---------------------------------------------------------------------------
// Sparkline chart
// ---------------------------------------------------------------------------

function SparklineChart({ series }: { series: { date: string; price: number }[] }) {
  if (!series.length) return null;

  const option = {
    backgroundColor: "transparent",
    grid: { top: 4, right: 4, bottom: 4, left: 4 },
    xAxis: { type: "time", show: false },
    yAxis: {
      type: "value",
      show: false,
      min: "dataMin",
      max: "dataMax",
    },
    tooltip: {
      trigger: "axis",
      backgroundColor: "var(--surface-2)",
      borderColor: "var(--border)",
      textStyle: { color: "var(--text-primary)", fontSize: 10, fontFamily: "var(--font-mono)" },
      formatter: (params: { value: [string, number] }[]) => {
        const p = params[0];
        if (!p?.value) return "";
        return `${p.value[0]}<br/>$${p.value[1].toLocaleString(undefined, { maximumFractionDigits: 2 })}`;
      },
    },
    series: [
      {
        type: "line",
        data: series.map((p) => [p.date, p.price]),
        symbol: "none",
        lineStyle: { color: "var(--accent)", width: 1.5 },
        areaStyle: {
          color: {
            type: "linear",
            x: 0, y: 0, x2: 0, y2: 1,
            colorStops: [
              { offset: 0, color: "var(--accent-alpha)" },
              { offset: 1, color: "transparent" },
            ],
          },
        },
        smooth: true,
      },
    ],
  };

  return (
    <ReactECharts
      option={option}
      style={{ height: 80 }}
      theme="dark"
      notMerge
    />
  );
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

interface Props {
  asset: AssetDetail | null;
  history: AssetMarketHistory | null;
}

export function MarketContextCard({ asset, history }: Props) {
  const market = asset?.market ?? null;
  const sparkData = (history?.series ?? [])
    .filter((p) => p.price_usd != null)
    .map((p) => ({
      date: p.snapshot_at.slice(0, 10),
      price: p.price_usd as number,
    }));

  return (
    <div className="mkt-card">
      <div className="mkt-card-header">
        <span className="mkt-card-title">Market Context</span>
        <span className="src-tag">CoinGecko</span>
      </div>

      {/* Sparkline */}
      {sparkData.length > 0 && (
        <div className="mkt-sparkline">
          <SparklineChart series={sparkData} />
        </div>
      )}

      {/* Key metrics grid */}
      <div className="mkt-metrics">
        <div className="mkt-metric">
          <span className="mkt-metric-label">Price</span>
          <span className="mkt-metric-value">{fmtUsd(market?.current_price_usd)}</span>
        </div>
        <div className="mkt-metric">
          <span className="mkt-metric-label">24h Change</span>
          <span
            className="mkt-metric-value"
            style={{ color: changeColor(market?.price_change_24h_pct ?? null) }}
          >
            {fmtPct(market?.price_change_24h_pct)}
          </span>
        </div>
        <div className="mkt-metric">
          <span className="mkt-metric-label">Market Cap</span>
          <span className="mkt-metric-value">{fmtUsd(market?.market_cap_usd, true)}</span>
        </div>
        <div className="mkt-metric">
          <span className="mkt-metric-label">24h Volume</span>
          <span className="mkt-metric-value">{fmtUsd(market?.volume_24h_usd, true)}</span>
        </div>
        <div className="mkt-metric">
          <span className="mkt-metric-label">FDV</span>
          <span className="mkt-metric-value">{fmtUsd(market?.fully_diluted_valuation_usd, true)}</span>
        </div>
        <div className="mkt-metric">
          <span className="mkt-metric-label">Circ. Supply</span>
          <span className="mkt-metric-value">{fmtSupply(market?.circulating_supply)}</span>
        </div>
      </div>

      {/* Supply bar */}
      {market?.circulating_supply != null && market?.max_supply != null && market.max_supply > 0 && (
        <div className="mkt-supply-bar-wrap">
          <div className="mkt-supply-bar-labels">
            <span>Circulating</span>
            <span>Max supply</span>
          </div>
          <div className="mkt-supply-bar-track">
            <div
              className="mkt-supply-bar-fill"
              style={{ width: `${Math.min(100, (market.circulating_supply / market.max_supply) * 100).toFixed(1)}%` }}
            />
          </div>
          <div className="mkt-supply-bar-labels" style={{ marginTop: 2 }}>
            <span style={{ color: "var(--text-secondary)" }}>
              {fmtSupply(market.circulating_supply)}
            </span>
            <span style={{ color: "var(--text-muted)" }}>
              {fmtSupply(market.max_supply)}
            </span>
          </div>
        </div>
      )}

      {/* Asset metadata */}
      {(asset?.name || asset?.asset_type) && (
        <div className="mkt-meta">
          {asset.name && <span className="mkt-meta-name">{asset.name}</span>}
          {asset.asset_type && (
            <span className="mkt-meta-badge">{asset.asset_type}</span>
          )}
        </div>
      )}
    </div>
  );
}
