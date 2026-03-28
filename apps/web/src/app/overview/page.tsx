import { GlobalMarketCard } from "@/components/overview/GlobalMarketCard";
import { MetricSection } from "@/components/overview/MetricSection";
import type { MetricRowData } from "@/components/overview/MetricRow";
import {
  fetchDerivativesOverview,
  fetchGlobalMarket,
  fetchLendingOverview,
  fetchBasisSnapshot,
  fetchDLYields,
  fetchDLProtocols,
  fetchDLStablecoins,
  fetchDLMarketContext,
  type BasisSnapshot,
  type DLYieldPool,
  type DLProtocol,
  type DLStablecoin,
  type DLMarketContext,
} from "@/lib/api";
import type {
  DerivativesOverview,
  FlatDerivativesRow,
  FlatLendingRow,
  LendingOverview,
} from "@/types/api";

export const revalidate = 120; // ISR: revalidate every 2 minutes

// -----------------------------------------------------------------------
// Formatting helpers
// -----------------------------------------------------------------------

function pct(value: number | null, decimals = 2): string {
  if (value == null) return "—";
  return `${value.toFixed(decimals)}%`;
}

function fundingPct(rate: number | null): string {
  if (rate == null) return "—";
  // Funding rates are per-8h; annualize: × 3 × 365
  const annualized = rate * 3 * 365 * 100;
  return `${annualized.toFixed(2)}%`;
}

function usd(value: number | null): string {
  if (value == null) return "—";
  if (value >= 1e9) return `$${(value / 1e9).toFixed(1)}B`;
  if (value >= 1e6) return `$${(value / 1e6).toFixed(0)}M`;
  return `$${value.toFixed(0)}`;
}

// -----------------------------------------------------------------------
// Data transforms — flatten API shapes into ranked MetricRowData[]
// -----------------------------------------------------------------------

function flattenLending(data: LendingOverview[]): FlatLendingRow[] {
  return data.flatMap((s) =>
    s.markets.map((m) => ({ ...m, asset: s.symbol }))
  );
}

function flattenDerivatives(data: DerivativesOverview[]): FlatDerivativesRow[] {
  return data.flatMap((s) =>
    s.venues.map((v) => ({ ...v, asset: s.symbol }))
  );
}

/** Minimum TVL (USD) for a market to appear in the overview rate tables. */
const MIN_TVL_USD = 5_000_000;

/**
 * Build a descriptive sub-label for a lending market row.
 *
 * Protocol labelling conventions:
 *   morpho_blue  — isolated pair model: market field is "COLLATERAL/LOAN"
 *                  → show "morpho · {collateral} [loan: LOAN]"
 *   kamino       — pool model: market field is the market name
 *                  → show "kamino · {market name}"
 *   aave         — pool model: market field is the deployment name
 *                  → show "aave · {chain}"
 *   others       — show protocol name
 *
 * None of our current ingestion includes vault data.  If vault rows are ever
 * added they should carry protocol="morpho_vault" or similar so they're
 * distinguishable here.
 */
function lendingSubLabel(r: FlatLendingRow): string {
  const { protocol, market, chain } = r;

  if (protocol === "morpho_blue") {
    // market format: "{collateral}/{loan}" e.g. "sUSN/USDC"
    const slash = market.indexOf("/");
    if (slash > 0) {
      const collateral = market.slice(0, slash);
      const loan = market.slice(slash + 1);
      // Only show "collateral [loan]" when collateral ≠ loan (avoids e.g. "USDT/USDT")
      return collateral === loan
        ? `morpho · ${market}`
        : `morpho · ${collateral} → ${loan}`;
    }
    return `morpho · ${market}`;
  }

  if (protocol === "kamino") {
    return `kamino · ${market}`;
  }

  if (protocol === "aave" || protocol === "aave-v3") {
    // market field is the deployment name ("AaveV3Ethereum") — chain is cleaner
    return `aave · ${chain ?? "Ethereum"}`;
  }

  return protocol;
}

function topBorrowRates(lending: FlatLendingRow[], n = 8): MetricRowData[] {
  // Deduplicate same-label markets — keep highest borrow APY
  const seen = new Map<string, FlatLendingRow>();
  for (const r of lending) {
    const key = `${r.asset}::${lendingSubLabel(r)}`;
    const prev = seen.get(key);
    if (!prev || (r.borrow_apy ?? 0) > (prev.borrow_apy ?? 0)) {
      seen.set(key, r);
    }
  }

  return [...seen.values()]
    .filter((r) => {
      if (r.borrow_apy == null || r.borrow_apy <= 0) return false;
      // Minimum TVL guard — skip tiny/illiquid markets
      if (r.tvl_usd != null && r.tvl_usd < MIN_TVL_USD) return false;
      // Skip fully-drained markets (100% utilized, no available liquidity)
      const hasLiquidity =
        r.available_liquidity_usd == null || r.available_liquidity_usd > 1000;
      const notFullyUtilized =
        r.utilization == null || r.utilization < 0.999;
      return hasLiquidity || notFullyUtilized;
    })
    .sort((a, b) => (b.borrow_apy ?? 0) - (a.borrow_apy ?? 0))
    .slice(0, n)
    .map((r, i) => ({
      rank: i + 1,
      asset: r.asset,
      subLabel: lendingSubLabel(r),
      chain: r.chain,
      value: pct(r.borrow_apy),
      valueSub: r.reward_borrow_apy ? `-${pct(r.reward_borrow_apy)} reward` : undefined,
      valueColor: "red",
      snapshotAt: r.snapshot_at,
      href: `/assets/${r.asset}`,
    }));
}

function topLendRates(lending: FlatLendingRow[], n = 8): MetricRowData[] {
  // Deduplicate: if multiple Morpho markets share the same collateral/loan label
  // (different LLTVs), keep only the one with the highest supply APY.
  const seen = new Map<string, FlatLendingRow>();
  for (const r of lending) {
    const key = `${r.asset}::${lendingSubLabel(r)}`;
    const prev = seen.get(key);
    if (!prev || (r.supply_apy ?? 0) > (prev.supply_apy ?? 0)) {
      seen.set(key, r);
    }
  }

  return [...seen.values()]
    .filter((r) => {
      if (r.supply_apy == null || r.supply_apy <= 0) return false;
      if (r.tvl_usd != null && r.tvl_usd < MIN_TVL_USD) return false;
      // Skip 100%-utilized markets — supply is trapped
      const notFullyUtilized = r.utilization == null || r.utilization < 0.999;
      return notFullyUtilized;
    })
    .sort((a, b) => (b.supply_apy ?? 0) - (a.supply_apy ?? 0))
    .slice(0, n)
    .map((r, i) => ({
      rank: i + 1,
      asset: r.asset,
      subLabel: lendingSubLabel(r),
      chain: r.chain,
      value: pct(r.supply_apy),
      valueSub: r.reward_supply_apy ? `+${pct(r.reward_supply_apy)} reward` : undefined,
      valueColor: "green",
      snapshotAt: r.snapshot_at,
      href: `/assets/${r.asset}`,
    }));
}

function topFunding(derivatives: FlatDerivativesRow[], n = 8): MetricRowData[] {
  return derivatives
    .filter((r) => r.funding_rate != null)
    .sort((a, b) => Math.abs(b.funding_rate ?? 0) - Math.abs(a.funding_rate ?? 0))
    .slice(0, n)
    .map((r, i) => {
      const rate = r.funding_rate ?? 0;
      return {
        rank: i + 1,
        asset: r.asset,
        subLabel: r.venue,
        chain: null,
        value: fundingPct(r.funding_rate),
        valueSub: `${(rate * 100).toFixed(4)}% / 8h`,
        valueColor: rate >= 0 ? "yellow" : "orange",
        snapshotAt: r.snapshot_at,
        href: `/assets/${r.asset}`,
      };
    });
}

function topBasisFromSnapshots(
  snapshots: (BasisSnapshot | null)[],
  n = 8,
): MetricRowData[] {
  const rows: MetricRowData[] = [];
  for (const snap of snapshots) {
    if (!snap) continue;
    for (const row of snap.term_structure) {
      if (row.basis_pct_ann == null) continue;
      rows.push({
        asset: snap.symbol,
        subLabel: `${row.venue} · ${row.contract}`,
        chain: null,
        value: pct(row.basis_pct_ann * 100),
        valueSub: `${row.days_to_expiry}d · OI ${row.oi_usd ? usd(row.oi_usd) : "—"}`,
        valueColor: row.basis_pct_ann >= 0 ? "yellow" : "orange",
        snapshotAt: snap.as_of,
        href: `/basis`,
      });
    }
  }
  return rows
    .sort((a, b) => {
      const va = parseFloat(a.value.replace("%", "")) || 0;
      const vb = parseFloat(b.value.replace("%", "")) || 0;
      return Math.abs(vb) - Math.abs(va);
    })
    .slice(0, n)
    .map((r, i) => ({ ...r, rank: i + 1 }));
}

function capacityConstraints(lending: FlatLendingRow[], n = 8): MetricRowData[] {
  // Highest utilization = tightest capacity constraint
  return lending
    .filter((r) => r.utilization != null)
    .sort((a, b) => (b.utilization ?? 0) - (a.utilization ?? 0))
    .slice(0, n)
    .map((r, i) => ({
      rank: i + 1,
      asset: r.asset,
      subLabel: `${r.protocol}`,
      chain: r.chain,
      value: pct((r.utilization ?? 0) * 100, 1),
      valueSub: r.available_liquidity_usd != null
        ? `avail ${usd(r.available_liquidity_usd)}`
        : undefined,
      valueColor: (r.utilization ?? 0) > 0.9 ? "red" : "orange",
      snapshotAt: r.snapshot_at,
      href: `/assets/${r.asset}`,
    }));
}

// -----------------------------------------------------------------------
// Page
// -----------------------------------------------------------------------

export default async function OverviewPage() {
  const [
    derivativesData,
    lendingData,
    globalMarket,
    basisBTC,
    basisETH,
    basisSOL,
    dlYields,
    dlProtocols,
    dlStables,
    dlMarket,
  ] = await Promise.all([
    fetchDerivativesOverview(["BTC", "ETH", "SOL"]),
    fetchLendingOverview(["USDC", "USDT", "ETH", "WBTC", "SOL", "DAI"]),
    fetchGlobalMarket(),
    fetchBasisSnapshot("BTC"),
    fetchBasisSnapshot("ETH"),
    fetchBasisSnapshot("SOL"),
    fetchDLYields(undefined, 5_000_000, 30),
    fetchDLProtocols(),
    fetchDLStablecoins(),
    fetchDLMarketContext(),
  ]);

  const lending = flattenLending(lendingData);
  const derivatives = flattenDerivatives(derivativesData);
  const basisRows = topBasisFromSnapshots([basisBTC, basisETH, basisSOL]);

  const now = new Date().toISOString();

  const sections = [
    {
      title: "Top Borrow Rates",
      titleColor: "red" as const,
      source: "Aave · Kamino · Morpho",
      rows: topBorrowRates(lending),
      emptyMessage: "No borrow data — run ingestion first",
    },
    {
      title: "Top Lend Rates",
      titleColor: "green" as const,
      source: "DeFiLlama",
      rows: topLendRates(lending),
      emptyMessage: "No supply data — run ingestion first",
    },
    {
      title: "Highest Funding",
      titleColor: "yellow" as const,
      source: "Internal / Velo",
      rows: topFunding(derivatives),
      emptyMessage: "Funding data loading — check the Funding page for live rates",
    },
    {
      title: "Highest Basis (Ann.)",
      titleColor: "yellow" as const,
      source: "Deribit · Bybit",
      rows: basisRows,
      emptyMessage: "No basis data — check the Basis page for live term structure",
    },
    {
      title: "Capacity Constraints",
      titleColor: "orange" as const,
      source: "DeFiLlama",
      rows: capacityConstraints(lending),
      emptyMessage: "No utilization data — run ingestion first",
    },
  ];

  // ── DefiLlama helpers ──────────────────────────────────────────────────
  const dexTotal = dlMarket?.context?.dex_volume?.aggregate;
  const oiTotal  = dlMarket?.context?.open_interest?.aggregate;
  const feesTotal = dlMarket?.context?.fees_revenue?.aggregate;

  const topDLYields = [...dlYields]
    .sort((a, b) => (b.apy ?? 0) - (a.apy ?? 0))
    .slice(0, 8);

  const topProtocols = [...dlProtocols]
    .sort((a, b) => (b.tvl_usd ?? 0) - (a.tvl_usd ?? 0))
    .slice(0, 8);

  const trackedStables = [...dlStables]
    .sort((a, b) => (b.circulating_usd ?? 0) - (a.circulating_usd ?? 0))
    .slice(0, 6);

  return (
    <>
      <div className="overview-header">
        <span className="overview-title">Market Overview</span>
        <span className="overview-refresh">
          Page rendered {new Date(now).toLocaleTimeString("en-US", { hour12: false })} UTC
          &nbsp;·&nbsp; auto-refresh every 2m
        </span>
      </div>
      <GlobalMarketCard data={globalMarket} />
      <div className="overview-grid">
        {sections.map((section) => (
          <MetricSection key={section.title} {...section} />
        ))}
      </div>

      {/* ── DefiLlama Market Intelligence ─────────────────────────────── */}
      <div className="dl-section">
        <div className="dl-section-header">
          <span className="dl-section-title">DeFi Market Intelligence</span>
          <span className="dl-badge">DefiLlama · free tier · no key</span>
        </div>

        {/* Market context summary cards */}
        <div className="dl-context-cards">
          <div className="dl-ctx-card">
            <span className="dl-ctx-label">DEX Volume 24h</span>
            <span className="dl-ctx-value">{dexTotal != null ? usd(dexTotal) : "—"}</span>
          </div>
          <div className="dl-ctx-card">
            <span className="dl-ctx-label">Perp Open Interest</span>
            <span className="dl-ctx-value">{oiTotal != null ? usd(oiTotal) : "—"}</span>
          </div>
          <div className="dl-ctx-card">
            <span className="dl-ctx-label">Fees &amp; Revenue 24h</span>
            <span className="dl-ctx-value">{feesTotal != null ? usd(feesTotal) : "—"}</span>
          </div>
        </div>

        <div className="dl-grid">
          {/* Top yield pools */}
          <div className="dl-card">
            <div className="dl-card-title">Top Yield Pools by APY</div>
            <table className="dl-table">
              <thead>
                <tr><th>Protocol</th><th>Symbol</th><th>Chain</th><th>APY</th><th>TVL</th></tr>
              </thead>
              <tbody>
                {topDLYields.length === 0 ? (
                  <tr><td colSpan={5} className="dl-empty">No pool data — run ingestion first</td></tr>
                ) : topDLYields.map((p) => (
                  <tr key={p.pool_id}>
                    <td>{p.project}</td>
                    <td><span className="dl-symbol">{p.symbol}</span></td>
                    <td>{p.chain}</td>
                    <td className="dl-apy">{p.apy != null ? `${p.apy.toFixed(2)}%` : "—"}</td>
                    <td>{p.tvl_usd != null ? usd(p.tvl_usd) : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* Protocol TVL */}
          <div className="dl-card">
            <div className="dl-card-title">Protocol TVL</div>
            <table className="dl-table">
              <thead>
                <tr><th>Protocol</th><th>Category</th><th>TVL</th><th>24h Δ</th><th>7d Δ</th></tr>
              </thead>
              <tbody>
                {topProtocols.length === 0 ? (
                  <tr><td colSpan={5} className="dl-empty">No protocol data — run ingestion first</td></tr>
                ) : topProtocols.map((p) => (
                  <tr key={p.protocol_slug}>
                    <td>{p.protocol_name}</td>
                    <td>{p.category ?? "—"}</td>
                    <td>{p.tvl_usd != null ? usd(p.tvl_usd) : "—"}</td>
                    <td className={p.change_1d != null && p.change_1d >= 0 ? "dl-pos" : "dl-neg"}>
                      {p.change_1d != null ? `${p.change_1d >= 0 ? "+" : ""}${p.change_1d.toFixed(1)}%` : "—"}
                    </td>
                    <td className={p.change_7d != null && p.change_7d >= 0 ? "dl-pos" : "dl-neg"}>
                      {p.change_7d != null ? `${p.change_7d >= 0 ? "+" : ""}${p.change_7d.toFixed(1)}%` : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* Stablecoin supply */}
          <div className="dl-card">
            <div className="dl-card-title">Stablecoin Supply</div>
            <table className="dl-table">
              <thead>
                <tr><th>Symbol</th><th>Circulating</th><th>Peg Type</th><th>Mechanism</th></tr>
              </thead>
              <tbody>
                {trackedStables.length === 0 ? (
                  <tr><td colSpan={4} className="dl-empty">No stablecoin data — run ingestion first</td></tr>
                ) : trackedStables.map((s) => (
                  <tr key={s.stablecoin_id}>
                    <td><span className="dl-symbol">{s.symbol}</span></td>
                    <td>{s.circulating_usd != null ? usd(s.circulating_usd) : "—"}</td>
                    <td>{s.peg_type ?? "—"}</td>
                    <td>{s.peg_mechanism ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </>
  );
}
