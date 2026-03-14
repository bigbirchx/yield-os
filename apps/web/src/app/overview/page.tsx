import { GlobalMarketCard } from "@/components/overview/GlobalMarketCard";
import { MetricSection } from "@/components/overview/MetricSection";
import type { MetricRowData } from "@/components/overview/MetricRow";
import { fetchDerivativesOverview, fetchGlobalMarket, fetchLendingOverview } from "@/lib/api";
import type {
  DerivativesOverview,
  FlatDerivativesRow,
  FlatLendingRow,
  LendingOverview,
} from "@/types/api";

export const revalidate = 60; // ISR: revalidate every 60 seconds

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

function topBorrowRates(lending: FlatLendingRow[], n = 8): MetricRowData[] {
  return lending
    .filter((r) => r.borrow_apy != null && r.borrow_apy > 0)
    .sort((a, b) => (b.borrow_apy ?? 0) - (a.borrow_apy ?? 0))
    .slice(0, n)
    .map((r, i) => ({
      rank: i + 1,
      asset: r.asset,
      subLabel: `${r.protocol}`,
      chain: r.chain,
      value: pct(r.borrow_apy),
      valueSub: r.reward_borrow_apy ? `-${pct(r.reward_borrow_apy)} reward` : undefined,
      valueColor: "red",
      snapshotAt: r.snapshot_at,
      href: `/assets/${r.asset}`,
    }));
}

function topLendRates(lending: FlatLendingRow[], n = 8): MetricRowData[] {
  return lending
    .filter((r) => r.supply_apy != null && r.supply_apy > 0)
    .sort((a, b) => (b.supply_apy ?? 0) - (a.supply_apy ?? 0))
    .slice(0, n)
    .map((r, i) => ({
      rank: i + 1,
      asset: r.asset,
      subLabel: `${r.protocol}`,
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

function topBasis(derivatives: FlatDerivativesRow[], n = 8): MetricRowData[] {
  return derivatives
    .filter((r) => r.basis_annualized != null)
    .sort((a, b) => Math.abs(b.basis_annualized ?? 0) - Math.abs(a.basis_annualized ?? 0))
    .slice(0, n)
    .map((r, i) => ({
      rank: i + 1,
      asset: r.asset,
      subLabel: r.venue,
      chain: null,
      value: pct(r.basis_annualized != null ? r.basis_annualized * 100 : null),
      valueSub: r.open_interest_usd ? `OI ${usd(r.open_interest_usd)}` : undefined,
      valueColor: (r.basis_annualized ?? 0) >= 0 ? "yellow" : "orange",
      snapshotAt: r.snapshot_at,
      href: `/assets/${r.asset}`,
    }));
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
  const [derivativesData, lendingData, globalMarket] = await Promise.all([
    fetchDerivativesOverview(["BTC", "ETH", "SOL"]),
    fetchLendingOverview(["USDC", "USDT", "ETH", "WBTC", "SOL", "DAI"]),
    fetchGlobalMarket(),
  ]);

  const lending = flattenLending(lendingData);
  const derivatives = flattenDerivatives(derivativesData);

  const now = new Date().toISOString();

  const sections = [
    {
      title: "Top Borrow Rates",
      titleColor: "red" as const,
      source: "DeFiLlama",
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
      title: "Highest Basis",
      titleColor: "yellow" as const,
      source: "Internal / Velo",
      rows: topBasis(derivatives),
      emptyMessage: "Basis data loading — check the Basis page for live term structure",
    },
    {
      title: "Capacity Constraints",
      titleColor: "orange" as const,
      source: "DeFiLlama",
      rows: capacityConstraints(lending),
      emptyMessage: "No utilization data — run ingestion first",
    },
  ];

  return (
    <>
      <div className="overview-header">
        <span className="overview-title">Market Overview</span>
        <span className="overview-refresh">
          Page rendered {new Date(now).toLocaleTimeString("en-US", { hour12: false })} UTC
          &nbsp;·&nbsp; auto-refresh every 60s
        </span>
      </div>
      <GlobalMarketCard data={globalMarket} />
      <div className="overview-grid">
        {sections.map((section) => (
          <MetricSection key={section.title} {...section} />
        ))}
      </div>
    </>
  );
}
