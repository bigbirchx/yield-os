"use client";
/**
 * Market Overview — the landing dashboard.
 *
 * Six sections answering "What's interesting right now?":
 *   1. Top Metrics Bar — best rates per umbrella, funding averages, total count
 *   2. Top Opportunities — highest supply yields · cheapest borrows · highest funding
 *   3. Market Heatmap — asset × venue supply APY intensity grid
 *   4. Rate Changes — 24h movers (rising / falling)
 *   5. Capacity Alerts — opportunities >90% filled
 *   6. Quick Navigation — cockpit links per umbrella + explorer
 *
 * All data from the unified /api/opportunities endpoint via SWR (60s polling).
 * History fetches for rate changes use a 5-minute refresh interval.
 */
import { useMemo } from "react";
import {
  useOpportunities,
  useOpportunitySummary,
  useOpportunityHistories,
} from "@/lib/hooks";
import type { MarketOpportunity, OpportunityRatePoint } from "@/types/api";
import {
  formatAPY,
  formatUSD,
  venueLabels,
  venueColors,
  umbrellaColors,
} from "@/lib/theme";

// ─── Types ────────────────────────────────────────────────────────────────────

interface RateChange {
  opp: MarketOpportunity;
  prevApy: number;
  change: number;
}

// ─── Pure helpers ─────────────────────────────────────────────────────────────

/** Short-form venue label for compact heatmap headers. */
const VENUE_SHORT: Record<string, string> = {
  COMPOUND_V3: "Comp.",
  JUSTLEND: "JustL.",
  JUPITER: "Jup.",
  KATANA: "Katana",
  ETHERFI: "ether.fi",
};

function venueShort(v: string): string {
  if (VENUE_SHORT[v]) return VENUE_SHORT[v];
  const label = venueLabels[v] ?? v;
  return label.length > 8 ? label.slice(0, 7) + "…" : label;
}

function getVenueLabel(v: string): string {
  return venueLabels[v] ?? v;
}

/** Extract available_liquidity_usd from the opaque liquidity field. */
function getLiquidityUsd(opp: MarketOpportunity): number | null {
  const liq = opp.liquidity as Record<string, number | null>;
  return liq?.available_liquidity_usd ?? null;
}

/** Most recent last_updated_at across all supplied opportunities. */
function getLastRefresh(opps: MarketOpportunity[]): string | null {
  if (opps.length === 0) return null;
  return opps.reduce(
    (latest, o) => (o.last_updated_at > latest ? o.last_updated_at : latest),
    opps[0].last_updated_at
  );
}

/** Human-readable relative time string from an ISO timestamp. */
function fmtRelative(iso: string | null): string {
  if (!iso) return "--";
  const diff = Date.now() - new Date(iso).getTime();
  const s = Math.floor(diff / 1000);
  if (s < 60) return "just now";
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ago`;
  return `${Math.floor(m / 60)}h ago`;
}

/** RGBA green color scaled by APY intensity; red for negative APY. */
function apyHeatColor(apy: number | null, maxApy: number): string {
  if (apy == null) return "transparent";
  if (apy < 0) return "rgba(239,68,68,0.18)";
  if (apy === 0) return "transparent";
  const t = Math.min(apy / Math.max(maxApy, 0.01), 1);
  return `rgba(34,197,94,${0.08 + t * 0.72})`;
}

/** Build the heatmap data structure from a flat list of supply opportunities. */
function buildHeatmap(opps: MarketOpportunity[]) {
  // Count how many supply opportunities exist per asset to rank them
  const assetCount = new Map<string, number>();
  for (const o of opps) {
    assetCount.set(o.asset_id, (assetCount.get(o.asset_id) ?? 0) + 1);
  }
  const topAssets = [...assetCount.entries()]
    .sort((a, b) => b[1] - a[1])
    .slice(0, 20)
    .map(([id]) => id);

  const topAssetSet = new Set(topAssets);

  // Collect venues that have supply opportunities for at least one top asset
  const venueSet = new Set<string>();
  for (const o of opps) {
    if (topAssetSet.has(o.asset_id)) venueSet.add(o.venue);
  }
  const venues = [...venueSet].sort();

  // matrix[assetId][venue] = best (max) supply APY
  const matrix = new Map<string, Map<string, number>>();
  for (const o of opps) {
    if (!topAssetSet.has(o.asset_id)) continue;
    if (!matrix.has(o.asset_id)) matrix.set(o.asset_id, new Map());
    const row = matrix.get(o.asset_id)!;
    const prev = row.get(o.venue);
    if (prev === undefined || o.total_apy_pct > prev) {
      row.set(o.venue, o.total_apy_pct);
    }
  }

  let maxApy = 0;
  for (const row of matrix.values()) {
    for (const v of row.values()) {
      if (v > maxApy) maxApy = v;
    }
  }

  return { topAssets, venues, matrix, maxApy };
}

/** Compute 24h APY changes from batched history data. */
function computeRateChanges(
  opps: MarketOpportunity[],
  histories: Record<string, OpportunityRatePoint[]>
): { rising: RateChange[]; falling: RateChange[] } {
  const cutoff = Date.now() - 86_400_000; // 24 hours ago
  const changes: RateChange[] = [];

  for (const opp of opps) {
    const hist = histories[opp.opportunity_id];
    if (!hist || hist.length < 2) continue;

    // Sort newest-first
    const sorted = [...hist].sort(
      (a, b) =>
        new Date(b.snapshot_at).getTime() - new Date(a.snapshot_at).getTime()
    );
    const latest = sorted[0];
    const prev = sorted.find(
      (p) => new Date(p.snapshot_at).getTime() <= cutoff
    );
    if (!prev) continue;

    const change = latest.total_apy_pct - prev.total_apy_pct;
    if (Math.abs(change) >= 0.25) {
      changes.push({ opp, prevApy: prev.total_apy_pct, change });
    }
  }

  return {
    rising: changes
      .filter((c) => c.change > 0)
      .sort((a, b) => b.change - a.change)
      .slice(0, 5),
    falling: changes
      .filter((c) => c.change < 0)
      .sort((a, b) => a.change - b.change)
      .slice(0, 5),
  };
}

// ─── Sub-components ────────────────────────────────────────────────────────────

function MetricCard({
  label,
  value,
  sub,
  accent,
}: {
  label: string;
  value: string;
  sub: string;
  accent?: string;
}) {
  return (
    <div className="ov-metric-card">
      <div className="ov-metric-label">{label}</div>
      <div
        className="ov-metric-value"
        style={accent ? { color: accent } : undefined}
      >
        {value}
      </div>
      <div className="ov-metric-sub">{sub}</div>
    </div>
  );
}

function OvPanel({
  title,
  titleColor,
  children,
}: {
  title: string;
  titleColor?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="ov-panel">
      <div className="ov-panel-header">
        <span className="ov-panel-title" style={titleColor ? { color: titleColor } : undefined}>
          {title}
        </span>
      </div>
      <div className="ov-panel-body">{children}</div>
    </div>
  );
}

function AssetCell({ opp }: { opp: MarketOpportunity }) {
  return (
    <a href={`/assets/${opp.asset_id}`} className="ov-asset-link">
      <span
        className="ov-umbrella-dot"
        style={{
          background: umbrellaColors[opp.umbrella_group] ?? "#64748b",
        }}
      />
      {opp.asset_symbol}
    </a>
  );
}

function VenueCell({ venue }: { venue: string }) {
  return (
    <span className="ov-venue-tag">
      <span
        className="ov-venue-dot"
        style={{ background: venueColors[venue] ?? "#475569" }}
      />
      {getVenueLabel(venue)}
    </span>
  );
}

// ─── Page ─────────────────────────────────────────────────────────────────────

export default function OverviewPage() {
  // ── Data fetching ─────────────────────────────────────────────────────────

  // Supply opportunities — sorted by APY desc; used for top yields, heatmap,
  // capacity alerts, rate changes, and umbrella-best metrics.
  const { data: supplyResult, isLoading: supplyLoading } = useOpportunities({
    side: "SUPPLY",
    exclude_amm_lp: true,
    sort_by: "total_apy_pct",
    limit: 200,
  });

  // Borrow opportunities — fetch a large page so cheapest borrows (lowest APY)
  // are reachable even without server-side ascending sort support.
  const { data: borrowResult, isLoading: borrowLoading } = useOpportunities({
    side: "BORROW",
    sort_by: "total_apy_pct",
    limit: 300,
  });

  // Funding rate opportunities (type = FUNDING_RATE from exchange adapters)
  const { data: fundingResult } = useOpportunities({
    type: "FUNDING_RATE",
    sort_by: "total_apy_pct",
    limit: 50,
  });

  const { data: summary } = useOpportunitySummary();

  const supplyOpps = supplyResult?.data ?? [];
  const borrowOpps = borrowResult?.data ?? [];
  const fundingOpps = fundingResult?.data ?? [];

  // ── Rate-change history (deferred; fires after supply data loads) ──────────

  const topIds = useMemo(
    () => supplyOpps.slice(0, 10).map((o) => o.opportunity_id),
    [supplyOpps]
  );
  const { data: histories } = useOpportunityHistories(topIds, 2);

  // ── Derived values ────────────────────────────────────────────────────────

  // Best supply rate per umbrella (supply is pre-sorted by APY desc → first match wins)
  const bestETH = supplyOpps.find((o) => o.umbrella_group === "ETH");
  const bestUSD = supplyOpps.find((o) => o.umbrella_group === "USD");
  const bestBTC = supplyOpps.find((o) => o.umbrella_group === "BTC");

  // Funding averages — annualized; total_apy_pct on FUNDING_RATE opps is already annualized
  const btcFundingOpps = fundingOpps.filter(
    (o) => o.umbrella_group === "BTC" || o.asset_id === "BTC"
  );
  const ethFundingOpps = fundingOpps.filter(
    (o) =>
      o.umbrella_group === "ETH" ||
      o.asset_id === "ETH" ||
      o.asset_id === "WETH"
  );
  const btcFundingAvg =
    btcFundingOpps.length > 0
      ? btcFundingOpps.reduce((s, o) => s + o.total_apy_pct, 0) /
        btcFundingOpps.length
      : null;
  const ethFundingAvg =
    ethFundingOpps.length > 0
      ? ethFundingOpps.reduce((s, o) => s + o.total_apy_pct, 0) /
        ethFundingOpps.length
      : null;

  const lastRefresh = useMemo(
    () => getLastRefresh([...supplyOpps, ...borrowOpps]),
    [supplyOpps, borrowOpps]
  );

  // Top opportunities lists
  const topSupply = supplyOpps.slice(0, 10);
  const cheapestBorrows = useMemo(
    () =>
      [...borrowOpps]
        .sort((a, b) => a.total_apy_pct - b.total_apy_pct)
        .slice(0, 10),
    [borrowOpps]
  );
  const topFunding = fundingOpps.slice(0, 10);

  // Heatmap
  const heatmap = useMemo(() => buildHeatmap(supplyOpps), [supplyOpps]);

  // Rate changes
  const rateChanges = useMemo(() => {
    if (!histories) return { rising: [], falling: [] };
    return computeRateChanges(supplyOpps.slice(0, 10), histories);
  }, [supplyOpps, histories]);

  // Capacity alerts — supply opps that are ≥90% of cap filled
  const capacityAlerts = useMemo(
    () =>
      supplyOpps
        .filter((o) => {
          if (
            !o.is_capacity_capped ||
            o.capacity_cap == null ||
            o.capacity_remaining == null ||
            o.capacity_cap <= 0
          )
            return false;
          return (
            (o.capacity_cap - o.capacity_remaining) / o.capacity_cap >= 0.9
          );
        })
        .slice(0, 12),
    [supplyOpps]
  );

  const totalOpps = summary?.total_opportunities;
  const isLoading = supplyLoading || borrowLoading;

  // ── Render ────────────────────────────────────────────────────────────────

  return (
    <div className="ov-page">
      {/* Header */}
      <div className="ov-page-header">
        <span className="ov-page-title">Market Overview</span>
        <div className="ov-page-header-right">
          <span className="ov-poll-tag">↻ 60s</span>
          <span className="ov-refresh-time">
            {isLoading ? "Updating…" : `Data: ${fmtRelative(lastRefresh)}`}
          </span>
        </div>
      </div>

      {/* ── 1. Top Metrics Bar ─────────────────────────────────────────────── */}
      <div className="ov-metrics-bar">
        <MetricCard
          label="Best ETH Supply"
          value={bestETH ? formatAPY(bestETH.total_apy_pct) : "--"}
          sub={bestETH ? getVenueLabel(bestETH.venue) : "no data"}
          accent="#627eea"
        />
        <MetricCard
          label="Best USD Supply"
          value={bestUSD ? formatAPY(bestUSD.total_apy_pct) : "--"}
          sub={bestUSD ? getVenueLabel(bestUSD.venue) : "no data"}
          accent="#22c55e"
        />
        <MetricCard
          label="Best BTC Supply"
          value={bestBTC ? formatAPY(bestBTC.total_apy_pct) : "--"}
          sub={bestBTC ? getVenueLabel(bestBTC.venue) : "no data"}
          accent="#f7931a"
        />
        <MetricCard
          label="BTC Funding Avg"
          value={btcFundingAvg != null ? formatAPY(btcFundingAvg) : "--"}
          sub={
            btcFundingOpps.length > 0
              ? `${btcFundingOpps.length} venues · ann.`
              : "no data"
          }
          accent="#f59e0b"
        />
        <MetricCard
          label="ETH Funding Avg"
          value={ethFundingAvg != null ? formatAPY(ethFundingAvg) : "--"}
          sub={
            ethFundingOpps.length > 0
              ? `${ethFundingOpps.length} venues · ann.`
              : "no data"
          }
          accent="#627eea"
        />
        <MetricCard
          label="Opportunities"
          value={totalOpps != null ? String(totalOpps) : "--"}
          sub="tracked live"
          accent="#3b82f6"
        />
        <MetricCard
          label="Last Refresh"
          value={fmtRelative(lastRefresh)}
          sub={isLoading ? "updating…" : "live · 60s poll"}
        />
      </div>

      {/* ── 2. Top Opportunities ────────────────────────────────────────────── */}
      <div className="ov-section">
        <div className="ov-section-header">
          <span className="ov-section-title">Top Opportunities</span>
        </div>
        <div className="ov-three-col">

          {/* Highest Supply Yields */}
          <OvPanel title="▲ Highest Supply Yields" titleColor="#22c55e">
            {isLoading && topSupply.length === 0 ? (
              <div className="ov-loading">Loading…</div>
            ) : topSupply.length === 0 ? (
              <div className="ov-empty">No supply data — run ingestion</div>
            ) : (
              <table className="ov-mini-table">
                <thead>
                  <tr>
                    <th>Asset</th>
                    <th>Venue</th>
                    <th className="ov-num-th">APY</th>
                    <th className="ov-num-th">TVL</th>
                  </tr>
                </thead>
                <tbody>
                  {topSupply.map((opp) => (
                    <tr key={opp.opportunity_id}>
                      <td>
                        <AssetCell opp={opp} />
                      </td>
                      <td>
                        <VenueCell venue={opp.venue} />
                      </td>
                      <td className="ov-num ov-apy-green">
                        {formatAPY(opp.total_apy_pct)}
                      </td>
                      <td className="ov-num ov-muted">
                        {opp.tvl_usd != null ? formatUSD(opp.tvl_usd) : "--"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </OvPanel>

          {/* Cheapest Borrows */}
          <OvPanel title="▼ Cheapest Borrows" titleColor="#ef4444">
            {borrowLoading && cheapestBorrows.length === 0 ? (
              <div className="ov-loading">Loading…</div>
            ) : cheapestBorrows.length === 0 ? (
              <div className="ov-empty">No borrow data</div>
            ) : (
              <table className="ov-mini-table">
                <thead>
                  <tr>
                    <th>Asset</th>
                    <th>Venue</th>
                    <th className="ov-num-th">Rate</th>
                    <th className="ov-num-th">Available</th>
                  </tr>
                </thead>
                <tbody>
                  {cheapestBorrows.map((opp) => (
                    <tr key={opp.opportunity_id}>
                      <td>
                        <AssetCell opp={opp} />
                      </td>
                      <td>
                        <VenueCell venue={opp.venue} />
                      </td>
                      <td className="ov-num ov-apy-red">
                        {formatAPY(opp.total_apy_pct)}
                      </td>
                      <td className="ov-num ov-muted">
                        {formatUSD(getLiquidityUsd(opp))}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </OvPanel>

          {/* Highest Funding */}
          <OvPanel title="◆ Highest Funding" titleColor="#f59e0b">
            {topFunding.length === 0 ? (
              <div className="ov-empty">No funding rate data</div>
            ) : (
              <table className="ov-mini-table">
                <thead>
                  <tr>
                    <th>Asset</th>
                    <th>Venue</th>
                    <th className="ov-num-th">Ann. Rate</th>
                  </tr>
                </thead>
                <tbody>
                  {topFunding.map((opp) => (
                    <tr key={opp.opportunity_id}>
                      <td>
                        <AssetCell opp={opp} />
                      </td>
                      <td className="ov-muted">
                        {getVenueLabel(opp.venue)}
                      </td>
                      <td
                        className="ov-num"
                        style={{
                          color:
                            opp.total_apy_pct >= 0 ? "#f59e0b" : "#f97316",
                          fontWeight: 600,
                        }}
                      >
                        {formatAPY(opp.total_apy_pct)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </OvPanel>
        </div>
      </div>

      {/* ── 3. Market Heatmap ───────────────────────────────────────────────── */}
      <div className="ov-section">
        <div className="ov-section-header">
          <span className="ov-section-title">Market Heatmap</span>
          <span className="ov-section-sub">
            Supply APY intensity · top {heatmap.topAssets.length} assets ·{" "}
            {heatmap.venues.length} venues · hover for exact rate
          </span>
        </div>

        {supplyLoading && heatmap.topAssets.length === 0 ? (
          <div className="ov-empty-panel">Building heatmap…</div>
        ) : heatmap.topAssets.length === 0 ? (
          <div className="ov-empty-panel">
            No heatmap data — run ingestion first
          </div>
        ) : (
          <div className="ov-heatmap-scroll">
            <table className="ov-heatmap-table">
              <thead>
                <tr>
                  <th className="ov-hm-corner">Asset</th>
                  {heatmap.venues.map((v) => (
                    <th
                      key={v}
                      className="ov-hm-col-header"
                      title={getVenueLabel(v)}
                    >
                      <span
                        className="ov-hm-venue-dot"
                        style={{ background: venueColors[v] ?? "#475569" }}
                      />
                      {venueShort(v)}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {heatmap.topAssets.map((assetId) => {
                  const row = heatmap.matrix.get(assetId);
                  return (
                    <tr key={assetId}>
                      <td className="ov-hm-row-header">
                        <a
                          href={`/assets/${assetId}`}
                          className="ov-asset-link"
                        >
                          {assetId}
                        </a>
                      </td>
                      {heatmap.venues.map((v) => {
                        const apy = row?.get(v) ?? null;
                        return (
                          <td
                            key={v}
                            className="ov-hm-cell"
                            style={{
                              background: apyHeatColor(apy, heatmap.maxApy),
                            }}
                            title={
                              apy != null
                                ? `${assetId} · ${getVenueLabel(v)}: ${formatAPY(apy)}`
                                : `${assetId} · ${getVenueLabel(v)}: no position`
                            }
                          >
                            {apy != null ? (
                              <span className="ov-hm-apy">
                                {formatAPY(apy)}
                              </span>
                            ) : (
                              <span className="ov-hm-empty">—</span>
                            )}
                          </td>
                        );
                      })}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* ── 4. Rate Changes ─────────────────────────────────────────────────── */}
      <div className="ov-section">
        <div className="ov-section-header">
          <span className="ov-section-title">Rate Changes (24h)</span>
          <span className="ov-section-sub">
            Top 10 supply opportunities · ±0.25% threshold
          </span>
        </div>

        {topIds.length === 0 ? (
          <div className="ov-empty-panel">
            Load supply data first to compute rate changes
          </div>
        ) : !histories ? (
          <div className="ov-empty-panel">Loading rate history…</div>
        ) : rateChanges.rising.length === 0 &&
          rateChanges.falling.length === 0 ? (
          <div className="ov-empty-panel">
            No significant rate changes detected in the last 24h
          </div>
        ) : (
          <div className="ov-two-col">
            <OvPanel title="▲ Rates Rising" titleColor="#22c55e">
              {rateChanges.rising.length === 0 ? (
                <div className="ov-empty">None in top 10</div>
              ) : (
                <table className="ov-mini-table">
                  <thead>
                    <tr>
                      <th>Asset</th>
                      <th>Venue</th>
                      <th className="ov-num-th">Now</th>
                      <th className="ov-num-th">24h Ago</th>
                      <th className="ov-num-th">Δ</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rateChanges.rising.map(({ opp, prevApy, change }) => (
                      <tr key={opp.opportunity_id}>
                        <td>
                          <AssetCell opp={opp} />
                        </td>
                        <td className="ov-muted">
                          {getVenueLabel(opp.venue)}
                        </td>
                        <td className="ov-num ov-apy-green">
                          {formatAPY(opp.total_apy_pct)}
                        </td>
                        <td className="ov-num ov-muted">
                          {formatAPY(prevApy)}
                        </td>
                        <td
                          className="ov-num"
                          style={{ color: "#22c55e", fontWeight: 600 }}
                        >
                          +{change.toFixed(2)}%
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </OvPanel>

            <OvPanel title="▼ Rates Falling" titleColor="#ef4444">
              {rateChanges.falling.length === 0 ? (
                <div className="ov-empty">None in top 10</div>
              ) : (
                <table className="ov-mini-table">
                  <thead>
                    <tr>
                      <th>Asset</th>
                      <th>Venue</th>
                      <th className="ov-num-th">Now</th>
                      <th className="ov-num-th">24h Ago</th>
                      <th className="ov-num-th">Δ</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rateChanges.falling.map(({ opp, prevApy, change }) => (
                      <tr key={opp.opportunity_id}>
                        <td>
                          <AssetCell opp={opp} />
                        </td>
                        <td className="ov-muted">
                          {getVenueLabel(opp.venue)}
                        </td>
                        <td className="ov-num ov-apy-green">
                          {formatAPY(opp.total_apy_pct)}
                        </td>
                        <td className="ov-num ov-muted">
                          {formatAPY(prevApy)}
                        </td>
                        <td
                          className="ov-num"
                          style={{ color: "#ef4444", fontWeight: 600 }}
                        >
                          {change.toFixed(2)}%
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </OvPanel>
          </div>
        )}
      </div>

      {/* ── 5. Capacity Alerts ──────────────────────────────────────────────── */}
      <div className="ov-section">
        <div className="ov-section-header">
          <span className="ov-section-title">Capacity Alerts</span>
          <span className="ov-section-sub">
            Opportunities &gt;90% of cap filled — may close to new deposits
          </span>
        </div>

        {capacityAlerts.length === 0 ? (
          <div className="ov-empty-panel">
            No near-capacity opportunities found
          </div>
        ) : (
          <div className="ov-cap-grid">
            {capacityAlerts.map((opp) => {
              const pct =
                opp.capacity_cap != null &&
                opp.capacity_remaining != null &&
                opp.capacity_cap > 0
                  ? ((opp.capacity_cap - opp.capacity_remaining) /
                      opp.capacity_cap) *
                    100
                  : null;
              return (
                <a
                  key={opp.opportunity_id}
                  href={`/assets/${opp.asset_id}`}
                  className="ov-cap-card"
                >
                  <div className="ov-cap-card-header">
                    <span
                      className="ov-umbrella-dot"
                      style={{
                        background:
                          umbrellaColors[opp.umbrella_group] ?? "#64748b",
                      }}
                    />
                    <span className="ov-cap-asset">{opp.asset_symbol}</span>
                    <span className="ov-cap-venue">
                      {getVenueLabel(opp.venue)}
                    </span>
                  </div>
                  <div className="ov-cap-bar-wrap">
                    <div
                      className="ov-cap-bar"
                      style={{ width: `${Math.min(pct ?? 0, 100)}%` }}
                    />
                  </div>
                  <div className="ov-cap-meta">
                    <span className="ov-cap-fill">
                      {pct != null ? `${pct.toFixed(1)}% filled` : "capped"}
                    </span>
                    <span className="ov-cap-apy ov-apy-green">
                      {formatAPY(opp.total_apy_pct)}
                    </span>
                  </div>
                </a>
              );
            })}
          </div>
        )}
      </div>

      {/* ── 6. Quick Navigation ─────────────────────────────────────────────── */}
      <div className="ov-section">
        <div className="ov-section-header">
          <span className="ov-section-title">Cockpit Navigation</span>
        </div>
        <div className="ov-nav-grid">
          {NAV_LINKS.map(({ href, label, color, desc }) => (
            <a key={href} href={href} className="ov-nav-card">
              <span
                className="ov-nav-card-dot"
                style={{ background: color }}
              />
              <div>
                <div className="ov-nav-card-label">{label}</div>
                <div className="ov-nav-card-desc">{desc}</div>
              </div>
            </a>
          ))}
        </div>
      </div>
    </div>
  );
}

// ─── Nav links config ────────────────────────────────────────────────────────

const NAV_LINKS = [
  {
    href: "/assets/BTC",
    label: "BTC Cockpit",
    color: "#f7931a",
    desc: "Bitcoin markets",
  },
  {
    href: "/assets/ETH",
    label: "ETH Cockpit",
    color: "#627eea",
    desc: "Ethereum markets",
  },
  {
    href: "/assets/SOL",
    label: "SOL Cockpit",
    color: "#9945ff",
    desc: "Solana markets",
  },
  {
    href: "/assets/USDC",
    label: "USD Cockpit",
    color: "#22c55e",
    desc: "Stablecoin markets",
  },
  {
    href: "/opportunities",
    label: "Opportunities",
    color: "#3b82f6",
    desc: "Full opportunity explorer",
  },
  {
    href: "/funding",
    label: "Funding Rates",
    color: "#f59e0b",
    desc: "Perpetual funding dashboard",
  },
  {
    href: "/basis",
    label: "Basis Trades",
    color: "#06b6d4",
    desc: "Futures term structure",
  },
  {
    href: "/tokens",
    label: "Token Universe",
    color: "#8b5cf6",
    desc: "Asset taxonomy & rankings",
  },
];
