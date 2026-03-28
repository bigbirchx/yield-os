"use client";

import { useState } from "react";
import { MetricSection } from "./MetricSection";
import type { MetricRowData } from "./MetricRow";
import type { FlatLendingRow } from "@/types/api";

// -----------------------------------------------------------------------
// Filter configuration — all defaults live here
// -----------------------------------------------------------------------

const TRACKED_ASSETS = ["USDC", "USDT", "ETH", "SOL", "BTC", "DAI"] as const;
const DEFAULT_ASSETS = new Set<string>(["USDC", "USDT", "ETH", "SOL", "BTC"]);

const TVL_PRESETS = [
  { label: "All", value: 0 },
  { label: "$1M",  value: 1_000_000 },
  { label: "$5M",  value: 5_000_000 },
  { label: "$10M", value: 10_000_000 },
  { label: "$50M", value: 50_000_000 },
];
const DEFAULT_MIN_TVL = 5_000_000;

const AVAIL_PRESETS = [
  { label: "All",   value: 0 },
  { label: "$100K", value: 100_000 },
  { label: "$500K", value: 500_000 },
  { label: "$1M",   value: 1_000_000 },
  { label: "$5M",   value: 5_000_000 },
];
const DEFAULT_MIN_AVAIL = 0;

// -----------------------------------------------------------------------
// Pure helpers
// -----------------------------------------------------------------------

function pct(v: number | null, d = 2) {
  return v != null ? `${v.toFixed(d)}%` : "—";
}

function usdShort(v: number | null) {
  if (v == null) return "—";
  if (v >= 1e9) return `$${(v / 1e9).toFixed(1)}B`;
  if (v >= 1e6) return `$${(v / 1e6).toFixed(0)}M`;
  if (v >= 1e3) return `$${(v / 1e3).toFixed(0)}K`;
  return `$${v.toFixed(0)}`;
}

export function lendingSubLabel(
  r: Pick<FlatLendingRow, "protocol" | "market" | "chain">,
): string {
  const { protocol, market, chain } = r;
  if (protocol === "morpho_blue") {
    const slash = market.indexOf("/");
    if (slash > 0) {
      const collateral = market.slice(0, slash);
      const loan = market.slice(slash + 1);
      return collateral === loan
        ? `morpho \u00B7 ${market}`
        : `morpho \u00B7 ${collateral} \u2192 ${loan}`;
    }
    return `morpho \u00B7 ${market}`;
  }
  if (protocol === "kamino") return `kamino \u00B7 ${market}`;
  if (protocol === "aave" || protocol === "aave-v3")
    return `aave \u00B7 ${chain ?? "Ethereum"}`;
  return protocol;
}

interface LendingFilters {
  minTvl: number;
  activeAssets: Set<string>;
  minAvailability: number;
}

function dedupByLabel(
  rows: FlatLendingRow[],
  key: (r: FlatLendingRow) => number | null,
): FlatLendingRow[] {
  const seen = new Map<string, FlatLendingRow>();
  for (const r of rows) {
    const k = `${r.asset}::${lendingSubLabel(r)}`;
    const prev = seen.get(k);
    if (!prev || (key(r) ?? 0) > (key(prev) ?? 0)) seen.set(k, r);
  }
  return [...seen.values()];
}

function topBorrowRates(
  lending: FlatLendingRow[],
  filters: LendingFilters,
  n = 8,
): MetricRowData[] {
  return dedupByLabel(lending, (r) => r.borrow_apy)
    .filter((r) => {
      if (r.borrow_apy == null || r.borrow_apy <= 0) return false;
      if (!filters.activeAssets.has(r.asset)) return false;
      if (filters.minTvl > 0 && r.tvl_usd != null && r.tvl_usd < filters.minTvl)
        return false;
      if (
        filters.minAvailability > 0 &&
        r.available_liquidity_usd != null &&
        r.available_liquidity_usd < filters.minAvailability
      )
        return false;
      const hasLiquidity =
        r.available_liquidity_usd == null || r.available_liquidity_usd > 1000;
      const notFullyUtilized = r.utilization == null || r.utilization < 0.999;
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
      valueSub: r.reward_borrow_apy
        ? `-${pct(r.reward_borrow_apy)} reward`
        : undefined,
      valueColor: "red" as const,
      snapshotAt: r.snapshot_at,
      href: `/assets/${r.asset}`,
    }));
}

function topLendRates(
  lending: FlatLendingRow[],
  filters: LendingFilters,
  n = 8,
): MetricRowData[] {
  return dedupByLabel(lending, (r) => r.supply_apy)
    .filter((r) => {
      if (r.supply_apy == null || r.supply_apy <= 0) return false;
      if (!filters.activeAssets.has(r.asset)) return false;
      if (filters.minTvl > 0 && r.tvl_usd != null && r.tvl_usd < filters.minTvl)
        return false;
      if (
        filters.minAvailability > 0 &&
        r.available_liquidity_usd != null &&
        r.available_liquidity_usd < filters.minAvailability
      )
        return false;
      return r.utilization == null || r.utilization < 0.999;
    })
    .sort((a, b) => (b.supply_apy ?? 0) - (a.supply_apy ?? 0))
    .slice(0, n)
    .map((r, i) => ({
      rank: i + 1,
      asset: r.asset,
      subLabel: lendingSubLabel(r),
      chain: r.chain,
      value: pct(r.supply_apy),
      valueSub: r.reward_supply_apy
        ? `+${pct(r.reward_supply_apy)} reward`
        : undefined,
      valueColor: "green" as const,
      snapshotAt: r.snapshot_at,
      href: `/assets/${r.asset}`,
    }));
}

function capacityConstraints(
  lending: FlatLendingRow[],
  filters: LendingFilters,
  n = 8,
): MetricRowData[] {
  return lending
    .filter((r) => {
      if (r.utilization == null) return false;
      if (!filters.activeAssets.has(r.asset)) return false;
      if (filters.minTvl > 0 && r.tvl_usd != null && r.tvl_usd < filters.minTvl)
        return false;
      return true;
    })
    .sort((a, b) => (b.utilization ?? 0) - (a.utilization ?? 0))
    .slice(0, n)
    .map((r, i) => ({
      rank: i + 1,
      asset: r.asset,
      subLabel: lendingSubLabel(r),
      chain: r.chain,
      value: pct((r.utilization ?? 0) * 100, 1),
      valueSub:
        r.available_liquidity_usd != null
          ? `avail ${usdShort(r.available_liquidity_usd)}`
          : undefined,
      valueColor: ((r.utilization ?? 0) > 0.9 ? "red" : "orange") as
        | "red"
        | "orange",
      snapshotAt: r.snapshot_at,
      href: `/assets/${r.asset}`,
    }));
}

// -----------------------------------------------------------------------
// Chip
// -----------------------------------------------------------------------

function Chip({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      className={active ? "lf-chip lf-chip-active" : "lf-chip"}
    >
      {children}
    </button>
  );
}

// -----------------------------------------------------------------------
// Main export
// -----------------------------------------------------------------------

export function LendingRateSections({
  lending,
}: {
  lending: FlatLendingRow[];
}) {
  const [minTvl, setMinTvl] = useState(DEFAULT_MIN_TVL);
  const [activeAssets, setActiveAssets] = useState<Set<string>>(
    new Set(DEFAULT_ASSETS),
  );
  const [minAvailability, setMinAvailability] = useState(DEFAULT_MIN_AVAIL);

  const filters: LendingFilters = { minTvl, activeAssets, minAvailability };

  function toggleAsset(sym: string) {
    setActiveAssets((prev) => {
      const next = new Set(prev);
      if (next.has(sym)) {
        if (next.size === 1) return prev;
        next.delete(sym);
      } else {
        next.add(sym);
      }
      return next;
    });
  }

  return (
    <div className="lf-wrapper">
      <div className="lf-bar">
        <div className="lf-group">
          <span className="lf-label">Min TVL</span>
          <div className="lf-chips">
            {TVL_PRESETS.map((p) => (
              <Chip
                key={p.value}
                active={minTvl === p.value}
                onClick={() => setMinTvl(p.value)}
              >
                {p.label}
              </Chip>
            ))}
          </div>
        </div>

        <div className="lf-group">
          <span className="lf-label">Asset</span>
          <div className="lf-chips">
            {TRACKED_ASSETS.map((sym) => (
              <Chip
                key={sym}
                active={activeAssets.has(sym)}
                onClick={() => toggleAsset(sym)}
              >
                {sym}
              </Chip>
            ))}
          </div>
        </div>

        <div className="lf-group">
          <span className="lf-label">Min Avail</span>
          <div className="lf-chips">
            {AVAIL_PRESETS.map((p) => (
              <Chip
                key={p.value}
                active={minAvailability === p.value}
                onClick={() => setMinAvailability(p.value)}
              >
                {p.label}
              </Chip>
            ))}
          </div>
        </div>
      </div>

      <div className="lf-grid">
        <MetricSection
          title="Top Borrow Rates"
          titleColor="red"
          source="Aave · Kamino · Morpho"
          rows={topBorrowRates(lending, filters)}
          emptyMessage="No borrow data matching filters — try relaxing Min TVL or Asset"
        />
        <MetricSection
          title="Top Lend Rates"
          titleColor="green"
          source="Aave · Kamino · Morpho · DeFiLlama"
          rows={topLendRates(lending, filters)}
          emptyMessage="No supply data matching filters — try relaxing Min TVL or Asset"
        />
        <MetricSection
          title="Capacity Constraints"
          titleColor="orange"
          source="Aave · Kamino · Morpho"
          rows={capacityConstraints(lending, filters)}
          emptyMessage="No utilization data matching filters — run ingestion first"
        />
      </div>
    </div>
  );
}
