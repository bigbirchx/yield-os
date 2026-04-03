"use client";

import { useState, useEffect, useMemo } from "react";
import DataTable, { type DataColumn } from "@/components/DataTable";
import RateChart, { type ChartSeries } from "@/components/RateChart";
import {
  formatAPY,
  formatUSD,
  getUmbrellaColor,
  venueLabels,
  subTypeLabels,
  chartColors,
  apyColor,
} from "@/lib/theme";
import type { MarketOpportunity, OpportunityRatePoint } from "@/types/api";

// ---------------------------------------------------------------------------
// Per-umbrella asset ribbon definitions
// ---------------------------------------------------------------------------

interface AssetChip {
  symbol: string;
  sub_type: string;
}

const UMBRELLA_ASSETS: Record<string, AssetChip[]> = {
  ETH: [
    { symbol: "ETH", sub_type: "NATIVE" },
    { symbol: "WETH", sub_type: "WRAPPED_NATIVE" },
    { symbol: "stETH", sub_type: "LIQUID_STAKING_TOKEN" },
    { symbol: "wstETH", sub_type: "LIQUID_STAKING_TOKEN" },
    { symbol: "cbETH", sub_type: "LIQUID_STAKING_TOKEN" },
    { symbol: "rETH", sub_type: "LIQUID_STAKING_TOKEN" },
    { symbol: "mETH", sub_type: "LIQUID_STAKING_TOKEN" },
    { symbol: "WBETH", sub_type: "LIQUID_STAKING_TOKEN" },
    { symbol: "eETH", sub_type: "LIQUID_RESTAKING_TOKEN" },
    { symbol: "weETH", sub_type: "LIQUID_RESTAKING_TOKEN" },
    { symbol: "rsETH", sub_type: "LIQUID_RESTAKING_TOKEN" },
    { symbol: "ezETH", sub_type: "LIQUID_RESTAKING_TOKEN" },
    { symbol: "pufETH", sub_type: "LIQUID_RESTAKING_TOKEN" },
  ],
  BTC: [
    { symbol: "BTC", sub_type: "NATIVE" },
    { symbol: "WBTC", sub_type: "WRAPPED_NATIVE" },
    { symbol: "CBBTC", sub_type: "WRAPPED_NATIVE" },
    { symbol: "BTCB", sub_type: "WRAPPED_NATIVE" },
    { symbol: "tBTC", sub_type: "LIQUID_STAKING_TOKEN" },
    { symbol: "LBTC", sub_type: "LIQUID_STAKING_TOKEN" },
    { symbol: "sBTC", sub_type: "LIQUID_STAKING_TOKEN" },
  ],
  USD: [
    { symbol: "USDC", sub_type: "TIER1_STABLE" },
    { symbol: "USDT", sub_type: "TIER1_STABLE" },
    { symbol: "DAI", sub_type: "TIER1_STABLE" },
    { symbol: "USDS", sub_type: "TIER1_STABLE" },
    { symbol: "PYUSD", sub_type: "TIER1_STABLE" },
    { symbol: "GHO", sub_type: "SYNTHETIC" },
    { symbol: "crvUSD", sub_type: "SYNTHETIC" },
    { symbol: "USDe", sub_type: "TOKENIZED_YIELD_STRATEGY" },
    { symbol: "sUSDe", sub_type: "TOKENIZED_YIELD_STRATEGY" },
    { symbol: "sDAI", sub_type: "RECEIPT_TOKEN" },
  ],
  SOL: [
    { symbol: "SOL", sub_type: "NATIVE" },
    { symbol: "mSOL", sub_type: "LIQUID_STAKING_TOKEN" },
    { symbol: "jitoSOL", sub_type: "LIQUID_STAKING_TOKEN" },
    { symbol: "bSOL", sub_type: "LIQUID_STAKING_TOKEN" },
    { symbol: "JupSOL", sub_type: "LIQUID_STAKING_TOKEN" },
    { symbol: "hSOL", sub_type: "LIQUID_STAKING_TOKEN" },
  ],
  HYPE: [{ symbol: "HYPE", sub_type: "NATIVE_TOKEN" }],
  OTHER: [],
};

const STAKING_UMBRELLAS = new Set(["ETH", "SOL", "BTC"]);

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

interface Props {
  umbrella: string;
  supplyOpps: MarketOpportunity[];
  borrowOpps: MarketOpportunity[];
  price: number | null;
}

// ---------------------------------------------------------------------------
// Cell helpers
// ---------------------------------------------------------------------------

function AssetCell({ symbol, subType }: { symbol: string; subType: string }) {
  const label = subTypeLabels[subType] ?? subType;
  return (
    <div className="ab-root ab-sm">
      <span className="ab-symbol">{symbol}</span>
      {label && (
        <span
          className="ab-tag"
          style={{ color: "var(--text-muted)", borderColor: "var(--border)" }}
        >
          {label}
        </span>
      )}
    </div>
  );
}

function VenueCell({ venue, chain }: { venue: string; chain: string }) {
  return (
    <div className="opp-venue-cell">
      <span style={{ fontSize: "12px", color: "var(--text-primary)" }}>
        {venueLabels[venue] ?? venue}
      </span>
      <span className="opp-chain-badge">{chain}</span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Supply columns
// ---------------------------------------------------------------------------

const supplyColumns: DataColumn<MarketOpportunity>[] = [
  {
    header: "Asset",
    accessorKey: "asset_symbol",
    width: "120px",
    cell: (_, row) => <AssetCell symbol={row.asset_symbol} subType={row.asset_sub_type} />,
  },
  {
    header: "Venue",
    accessorKey: "venue",
    width: "130px",
    cell: (_, row) => <VenueCell venue={row.venue} chain={row.chain} />,
  },
  {
    header: "Type",
    accessorKey: "opportunity_type",
    width: "100px",
    cell: (val) => (
      <span className="opp-type-badge">{String(val).replace(/_/g, " ")}</span>
    ),
  },
  {
    header: "Total APY",
    accessorKey: "total_apy_pct",
    width: "90px",
    align: "right",
    mono: true,
    cell: (val) => (
      <span className="opp-apy" style={{ color: apyColor(val as number) }}>
        {formatAPY(val as number)}
      </span>
    ),
  },
  {
    header: "Base APY",
    accessorKey: "base_apy_pct",
    width: "80px",
    align: "right",
    mono: true,
    cell: (val) => <span style={{ color: "var(--text-secondary)" }}>{formatAPY(val as number)}</span>,
  },
  {
    header: "Rewards",
    accessorFn: (row) =>
      (row.reward_breakdown ?? []).reduce((s, r) => s + (r.apy_pct ?? 0), 0),
    width: "75px",
    align: "right",
    mono: true,
    cell: (val) => {
      const v = val as number;
      return v > 0 ? (
        <span style={{ color: "var(--yellow)" }}>{formatAPY(v)}</span>
      ) : (
        <span className="dt-null">--</span>
      );
    },
  },
  {
    header: "TVL",
    accessorKey: "tvl_usd",
    width: "95px",
    align: "right",
    mono: true,
    cell: (val) => <span style={{ color: "var(--text-secondary)" }}>{formatUSD(val as number | null)}</span>,
  },
  {
    header: "Capacity",
    accessorKey: "capacity_remaining",
    width: "90px",
    align: "right",
    mono: true,
    cell: (val, row) => {
      if (!row.is_capacity_capped) return <span className="dt-null">uncapped</span>;
      return (
        <span style={{ color: (val as number | null) != null && (val as number) < 1e6 ? "var(--yellow)" : "var(--text-secondary)" }}>
          {formatUSD(val as number | null)}
        </span>
      );
    },
  },
  {
    header: "LTV",
    accessorKey: "as_collateral_max_ltv_pct",
    width: "60px",
    align: "right",
    mono: true,
    cell: (val, row) => {
      if (!row.is_collateral_eligible) return <span className="dt-null">--</span>;
      return <span style={{ color: "var(--text-secondary)" }}>{val != null ? `${(val as number).toFixed(0)}%` : "--"}</span>;
    },
  },
  {
    header: "Receipt",
    accessorFn: (row) => row.receipt_token != null,
    width: "60px",
    align: "center",
    sortable: false,
    cell: (val) =>
      val ? (
        <span style={{ color: "var(--accent)", fontSize: "10px" }}>✓</span>
      ) : (
        <span className="dt-null">--</span>
      ),
  },
];

// ---------------------------------------------------------------------------
// Borrow columns
// ---------------------------------------------------------------------------

const borrowColumns: DataColumn<MarketOpportunity>[] = [
  {
    header: "Asset",
    accessorKey: "asset_symbol",
    width: "120px",
    cell: (_, row) => <AssetCell symbol={row.asset_symbol} subType={row.asset_sub_type} />,
  },
  {
    header: "Venue",
    accessorKey: "venue",
    width: "130px",
    cell: (_, row) => <VenueCell venue={row.venue} chain={row.chain} />,
  },
  {
    header: "Borrow APY",
    accessorKey: "total_apy_pct",
    width: "95px",
    align: "right",
    mono: true,
    cell: (val) => (
      <span className="opp-apy" style={{ color: "var(--red)" }}>
        {formatAPY(val as number)}
      </span>
    ),
  },
  {
    header: "Available",
    accessorFn: (row) =>
      (row.liquidity as { available_liquidity_usd?: number } | null)?.available_liquidity_usd ?? null,
    width: "110px",
    align: "right",
    mono: true,
    cell: (val) => (
      <span style={{ color: "var(--text-secondary)" }}>
        {formatUSD(val as number | null)}
      </span>
    ),
  },
  {
    header: "Utilization",
    accessorFn: (row) =>
      (row.liquidity as { utilization_rate_pct?: number } | null)?.utilization_rate_pct ?? null,
    width: "90px",
    align: "right",
    mono: true,
    cell: (val) => {
      const v = val as number | null;
      const color = v != null && v > 90 ? "var(--red)" : v != null && v > 75 ? "var(--yellow)" : "var(--text-secondary)";
      return <span style={{ color }}>{v != null ? `${v.toFixed(1)}%` : "--"}</span>;
    },
  },
  {
    header: "Cap Remaining",
    accessorKey: "capacity_remaining",
    width: "110px",
    align: "right",
    mono: true,
    cell: (val, row) => {
      if (!row.is_capacity_capped) return <span className="dt-null">uncapped</span>;
      return <span style={{ color: "var(--text-secondary)" }}>{formatUSD(val as number | null)}</span>;
    },
  },
  {
    header: "Collateral",
    accessorFn: (row) => (row.collateral_options ?? []).length,
    width: "80px",
    align: "right",
    mono: true,
    cell: (val) =>
      (val as number) > 0 ? (
        <span style={{ color: "var(--text-muted)", fontSize: "11px" }}>{val as number} assets</span>
      ) : (
        <span className="dt-null">--</span>
      ),
  },
];

// ---------------------------------------------------------------------------
// Staking columns (supply opps that are staking/restaking/LST/LRT)
// ---------------------------------------------------------------------------

const stakingColumns: DataColumn<MarketOpportunity>[] = [
  {
    header: "Protocol",
    accessorKey: "protocol",
    width: "160px",
    cell: (_, row) => (
      <div className="opp-venue-cell">
        <span>{row.protocol}</span>
        <span className="opp-chain-badge">{venueLabels[row.venue] ?? row.venue}</span>
      </div>
    ),
  },
  {
    header: "Asset",
    accessorKey: "asset_symbol",
    width: "100px",
    cell: (_, row) => <AssetCell symbol={row.asset_symbol} subType={row.asset_sub_type} />,
  },
  {
    header: "APY",
    accessorKey: "total_apy_pct",
    width: "80px",
    align: "right",
    mono: true,
    cell: (val) => (
      <span className="opp-apy" style={{ color: apyColor(val as number) }}>
        {formatAPY(val as number)}
      </span>
    ),
  },
  {
    header: "TVL",
    accessorKey: "tvl_usd",
    width: "100px",
    align: "right",
    mono: true,
    cell: (val) => <span style={{ color: "var(--text-secondary)" }}>{formatUSD(val as number | null)}</span>,
  },
];

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export default function UmbrellaCockpitClient({ umbrella, supplyOpps, borrowOpps, price }: Props) {
  const [selectedAsset, setSelectedAsset] = useState<string | null>(null);
  const [expandedBorrowId, setExpandedBorrowId] = useState<string | null>(null);
  const [historySeries, setHistorySeries] = useState<ChartSeries[]>([]);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [showConversions, setShowConversions] = useState(false);

  const umbrellaColor = getUmbrellaColor(umbrella);

  // Asset chips: use hardcoded list filtered to only those that appear in actual data
  const activeSymbols = useMemo(() => {
    const set = new Set<string>();
    for (const o of supplyOpps) set.add(o.asset_symbol);
    for (const o of borrowOpps) set.add(o.asset_symbol);
    return set;
  }, [supplyOpps, borrowOpps]);

  const assetChips = useMemo(() => {
    const defined = UMBRELLA_ASSETS[umbrella] ?? [];
    const chips = defined.filter((c) => activeSymbols.has(c.symbol));
    // Also add any symbols from live data not in the hardcoded list
    for (const sym of activeSymbols) {
      if (!chips.find((c) => c.symbol === sym)) {
        chips.push({ symbol: sym, sub_type: "NATIVE_TOKEN" });
      }
    }
    return chips;
  }, [umbrella, activeSymbols]);

  // Filtered opportunities
  const filteredSupply = useMemo(
    () => (selectedAsset ? supplyOpps.filter((o) => o.asset_symbol === selectedAsset) : supplyOpps),
    [supplyOpps, selectedAsset]
  );

  const filteredBorrow = useMemo(
    () => (selectedAsset ? borrowOpps.filter((o) => o.asset_symbol === selectedAsset) : borrowOpps),
    [borrowOpps, selectedAsset]
  );

  // Supply subtotals
  const supplyStats = useMemo(() => {
    if (filteredSupply.length === 0) return null;
    const totalTvl = filteredSupply.reduce((s, o) => s + (o.tvl_usd ?? 0), 0);
    const avgApy =
      filteredSupply.reduce((s, o) => s + o.total_apy_pct, 0) / filteredSupply.length;
    const topApy = Math.max(...filteredSupply.map((o) => o.total_apy_pct));
    return { totalTvl, avgApy, topApy, count: filteredSupply.length };
  }, [filteredSupply]);

  // Staking opps (for ETH/SOL/BTC umbrellas)
  const stakingOpps = useMemo(
    () =>
      supplyOpps.filter(
        (o) =>
          o.opportunity_type === "STAKING" ||
          o.opportunity_type === "RESTAKING" ||
          o.asset_sub_type === "LIQUID_STAKING_TOKEN" ||
          o.asset_sub_type === "LIQUID_RESTAKING_TOKEN"
      ),
    [supplyOpps]
  );

  // Fetch history for top 5 supply opps by TVL
  useEffect(() => {
    const top5 = [...supplyOpps]
      .filter((o) => o.tvl_usd != null && o.tvl_usd > 0)
      .sort((a, b) => (b.tvl_usd ?? 0) - (a.tvl_usd ?? 0))
      .slice(0, 5);

    if (top5.length === 0) return;

    setHistoryLoading(true);
    const apiUrl = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

    Promise.all(
      top5.map((opp, i) =>
        fetch(
          `${apiUrl}/api/opportunities/${encodeURIComponent(opp.opportunity_id)}/history?days=90`
        )
          .then((r) => (r.ok ? (r.json() as Promise<OpportunityRatePoint[]>) : []))
          .catch(() => [] as OpportunityRatePoint[])
          .then((data) => ({
            name: `${opp.asset_symbol} · ${venueLabels[opp.venue] ?? opp.venue}`,
            data: data.map((p) => ({ date: p.snapshot_at, value: p.total_apy_pct })),
            color: chartColors[i % chartColors.length],
          }))
      )
    ).then((series) => {
      setHistorySeries(series.filter((s) => s.data.length > 0));
      setHistoryLoading(false);
    });
  }, [supplyOpps]);

  // Expanded borrow row: find the opportunity
  const expandedBorrowOpp = useMemo(
    () => (expandedBorrowId ? borrowOpps.find((o) => o.opportunity_id === expandedBorrowId) ?? null : null),
    [expandedBorrowId, borrowOpps]
  );

  const collateralOptions = expandedBorrowOpp?.collateral_options ?? [];

  return (
    <div className="asset-page">
      {/* ── Header ──────────────────────────────────────────────────────── */}
      <header className="asset-header">
        <div className="asset-header-left">
          <span
            style={{
              display: "inline-block",
              width: 10,
              height: 10,
              borderRadius: "50%",
              background: umbrellaColor,
              marginRight: 4,
              flexShrink: 0,
            }}
          />
          <h1 className="asset-symbol" style={{ color: umbrellaColor }}>
            {umbrella}
          </h1>
          {price != null && (
            <span className="asset-mark-price">
              {umbrella === "USD"
                ? "$1.000"
                : `$${price.toLocaleString("en-US", { maximumFractionDigits: 2 })}`}
            </span>
          )}
          <span
            style={{
              fontSize: "11px",
              color: "var(--text-muted)",
              fontFamily: "var(--font-mono)",
            }}
          >
            Umbrella Group
          </span>
        </div>
        <div className="asset-header-right">
          <span className="asset-snapshot-ts">
            {supplyOpps.length + borrowOpps.length} opportunities
          </span>
        </div>
      </header>

      {/* ── Related assets ribbon ────────────────────────────────────── */}
      {assetChips.length > 1 && (
        <div
          style={{
            display: "flex",
            flexWrap: "wrap",
            alignItems: "center",
            gap: "0.35rem",
            marginBottom: "0.25rem",
          }}
        >
          <button
            className={`fb-chip fb-chip-sm ${selectedAsset === null ? "fb-chip-active" : ""}`}
            onClick={() => setSelectedAsset(null)}
          >
            All
          </button>
          {assetChips.map((chip) => (
            <button
              key={chip.symbol}
              className={`fb-chip fb-chip-sm ${selectedAsset === chip.symbol ? "fb-chip-active" : ""}`}
              onClick={() =>
                setSelectedAsset(selectedAsset === chip.symbol ? null : chip.symbol)
              }
              title={subTypeLabels[chip.sub_type] ?? chip.sub_type}
            >
              {chip.symbol}
              {chip.sub_type && (
                <span style={{ opacity: 0.65, marginLeft: 3 }}>
                  {subTypeLabels[chip.sub_type] ?? ""}
                </span>
              )}
            </button>
          ))}
        </div>
      )}

      {/* ── Supply opportunities ─────────────────────────────────────── */}
      <section className="section-card">
        <div className="section-card-header">
          <span className="section-card-title">
            Supply Opportunities{" "}
            <span style={{ color: "var(--text-muted)", fontWeight: 400 }}>
              ({filteredSupply.length})
            </span>
          </span>
          {supplyStats && (
            <span
              style={{
                fontFamily: "var(--font-mono)",
                fontSize: "11px",
                color: "var(--text-muted)",
              }}
            >
              avg {formatAPY(supplyStats.avgApy)} · top {formatAPY(supplyStats.topApy)} · TVL{" "}
              {formatUSD(supplyStats.totalTvl)}
            </span>
          )}
        </div>
        <div className="section-card-body">
          <DataTable
            columns={supplyColumns}
            data={filteredSupply}
            defaultSortKey="total_apy_pct"
            defaultSortDesc
            emptyMessage="No supply opportunities"
          />
        </div>
        {supplyStats && (
          <div
            style={{
              display: "flex",
              gap: "2rem",
              padding: "0.5rem 0.75rem",
              borderTop: "1px solid var(--border-subtle)",
              background: "var(--surface-2)",
              fontSize: "11px",
              fontFamily: "var(--font-mono)",
              color: "var(--text-muted)",
            }}
          >
            <span>
              <strong style={{ color: "var(--text-secondary)" }}>{supplyStats.count}</strong> markets
            </span>
            <span>
              Avg APY:{" "}
              <strong style={{ color: apyColor(supplyStats.avgApy) }}>
                {formatAPY(supplyStats.avgApy)}
              </strong>
            </span>
            <span>
              Top APY:{" "}
              <strong style={{ color: apyColor(supplyStats.topApy) }}>
                {formatAPY(supplyStats.topApy)}
              </strong>
            </span>
            <span>
              Total TVL:{" "}
              <strong style={{ color: "var(--text-secondary)" }}>
                {formatUSD(supplyStats.totalTvl)}
              </strong>
            </span>
          </div>
        )}
      </section>

      {/* ── Borrow opportunities ─────────────────────────────────────── */}
      <section className="section-card">
        <div className="section-card-header">
          <span className="section-card-title">
            Borrow Opportunities{" "}
            <span style={{ color: "var(--text-muted)", fontWeight: 400 }}>
              ({filteredBorrow.length})
            </span>
          </span>
          {expandedBorrowId && (
            <button
              className="fb-chip fb-chip-sm"
              onClick={() => setExpandedBorrowId(null)}
              style={{ fontSize: "10px" }}
            >
              ✕ Close detail
            </button>
          )}
        </div>
        <div className="section-card-body">
          <DataTable
            columns={borrowColumns}
            data={filteredBorrow}
            defaultSortKey="total_apy_pct"
            defaultSortDesc={false}
            emptyMessage="No borrow opportunities"
            onRowClick={(row) =>
              setExpandedBorrowId(
                expandedBorrowId === row.opportunity_id ? null : row.opportunity_id
              )
            }
            getRowClassName={(row) =>
              row.opportunity_id === expandedBorrowId ? "dt-row-selected" : ""
            }
          />
        </div>

        {/* Collateral matrix detail panel */}
        {expandedBorrowOpp && (
          <div
            style={{
              borderTop: "1px solid var(--border)",
              padding: "0.75rem 1rem",
              background: "var(--surface-2)",
            }}
          >
            <div
              style={{
                fontSize: "10px",
                fontWeight: 600,
                letterSpacing: "0.1em",
                textTransform: "uppercase",
                color: "var(--text-muted)",
                marginBottom: "0.5rem",
              }}
            >
              Collateral Matrix — {expandedBorrowOpp.asset_symbol} on{" "}
              {venueLabels[expandedBorrowOpp.venue] ?? expandedBorrowOpp.venue} ·{" "}
              {expandedBorrowOpp.chain}
            </div>
            {collateralOptions.length === 0 ? (
              <p style={{ fontSize: "11px", color: "var(--text-muted)" }}>
                No collateral options available
              </p>
            ) : (
              <div style={{ overflowX: "auto" }}>
                <table
                  style={{
                    width: "100%",
                    borderCollapse: "collapse",
                    fontSize: "11px",
                    fontFamily: "var(--font-mono)",
                  }}
                >
                  <thead>
                    <tr>
                      {["Collateral", "Max LTV", "Liq. LTV", "Liq. Penalty"].map((h) => (
                        <th
                          key={h}
                          style={{
                            textAlign: h === "Collateral" ? "left" : "right",
                            padding: "0.25rem 0.5rem",
                            fontSize: "10px",
                            fontWeight: 600,
                            textTransform: "uppercase",
                            letterSpacing: "0.05em",
                            color: "var(--text-muted)",
                            borderBottom: "1px solid var(--border)",
                          }}
                        >
                          {h}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {collateralOptions.map((col, i) => {
                      const c = col as {
                        asset_id?: string;
                        max_ltv_pct?: number;
                        liquidation_ltv_pct?: number;
                        liquidation_penalty_pct?: number;
                      };
                      return (
                        <tr key={i}>
                          <td style={{ padding: "0.2rem 0.5rem", color: "var(--text-primary)" }}>
                            {c.asset_id ?? "–"}
                          </td>
                          <td
                            style={{
                              padding: "0.2rem 0.5rem",
                              textAlign: "right",
                              color: "var(--text-secondary)",
                            }}
                          >
                            {c.max_ltv_pct != null ? `${c.max_ltv_pct.toFixed(0)}%` : "–"}
                          </td>
                          <td
                            style={{
                              padding: "0.2rem 0.5rem",
                              textAlign: "right",
                              color: "var(--text-secondary)",
                            }}
                          >
                            {c.liquidation_ltv_pct != null
                              ? `${c.liquidation_ltv_pct.toFixed(0)}%`
                              : "–"}
                          </td>
                          <td
                            style={{
                              padding: "0.2rem 0.5rem",
                              textAlign: "right",
                              color: "var(--yellow)",
                            }}
                          >
                            {c.liquidation_penalty_pct != null
                              ? `${c.liquidation_penalty_pct.toFixed(1)}%`
                              : "–"}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        )}
      </section>

      {/* ── Historical rates chart ───────────────────────────────────── */}
      <section className="section-card">
        <div className="section-card-header">
          <span className="section-card-title">Historical Supply APY — Top 5 Markets by TVL</span>
          {historyLoading && (
            <span style={{ fontSize: "10px", color: "var(--text-muted)" }}>Loading…</span>
          )}
        </div>
        <div className="section-card-body" style={{ padding: "0.5rem 0" }}>
          {!historyLoading && historySeries.length === 0 ? (
            <div className="section-empty">No historical data available</div>
          ) : (
            <RateChart
              series={historySeries}
              height={300}
              showRangeSelector
              defaultDays={30}
              yAxisLabel="APY %"
            />
          )}
        </div>
      </section>

      {/* ── Staking / Wrapping overview (ETH / SOL / BTC) ───────────── */}
      {STAKING_UMBRELLAS.has(umbrella) && stakingOpps.length > 0 && (
        <section className="section-card">
          <div className="section-card-header">
            <span className="section-card-title">
              Staking &amp; Wrapping —{" "}
              {umbrella === "ETH"
                ? "LSTs and LRTs"
                : umbrella === "SOL"
                ? "Liquid Staking Tokens"
                : "Wrapped BTC Yields"}
            </span>
            <span
              style={{
                fontFamily: "var(--font-mono)",
                fontSize: "11px",
                color: "var(--text-muted)",
              }}
            >
              {stakingOpps.length} protocols
            </span>
          </div>
          <div className="section-card-body">
            <DataTable
              columns={stakingColumns}
              data={stakingOpps}
              defaultSortKey="total_apy_pct"
              defaultSortDesc
              emptyMessage="No staking data"
            />
          </div>
        </section>
      )}

      {/* ── Conversion paths (collapsible) ──────────────────────────── */}
      <section className="section-card">
        <button
          className="section-card-header"
          style={{
            width: "100%",
            background: "none",
            border: "none",
            cursor: "pointer",
            textAlign: "left",
          }}
          onClick={() => setShowConversions((v) => !v)}
        >
          <span className="section-card-title">Conversion Paths</span>
          <span style={{ fontSize: "10px", color: "var(--text-muted)", marginLeft: "auto" }}>
            {showConversions ? "▲" : "▼"}
          </span>
        </button>
        {showConversions && (
          <div
            className="section-card-body"
            style={{ padding: "1rem", fontSize: "12px", color: "var(--text-muted)" }}
          >
            <p>
              Conversion paths between{" "}
              <span style={{ color: umbrellaColor, fontWeight: 600 }}>{umbrella}</span>-family
              assets show available routes (wrapping, staking, bridging) with estimated cost and
              steps. This section is populated when the conversion paths API is available.
            </p>
            <p style={{ marginTop: "0.5rem", fontFamily: "var(--font-mono)", fontSize: "11px" }}>
              GET /api/assets/conversions?umbrella={umbrella} — coming soon
            </p>
          </div>
        )}
      </section>
    </div>
  );
}
