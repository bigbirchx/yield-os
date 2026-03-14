import { notFound } from "next/navigation";

import { BorrowDemandCard } from "@/components/asset/BorrowDemandCard";
import { DerivativesTable } from "@/components/asset/DerivativesTable";
import { EventsSection } from "@/components/asset/EventsSection";
import { HistoryChart } from "@/components/asset/HistoryChart";
import { LendingTable } from "@/components/asset/LendingTable";
import { RiskParamsTable } from "@/components/asset/RiskParamsTable";
import { SectionCard } from "@/components/asset/SectionCard";
import { StakingTable } from "@/components/asset/StakingTable";
import { TransformsSection } from "@/components/asset/TransformsSection";
import { SourceTag } from "@/components/overview/SourceTag";
import {
  fetchAssetDerivatives,
  fetchAssetHistory,
  fetchAssetLending,
  fetchAssetStaking,
  fetchBorrowDemand,
  fetchLtvMatrix,
} from "@/lib/api";
import type { LendingHistoryMarket } from "@/types/api";

export const revalidate = 60;

const VALID_SYMBOLS = ["BTC", "ETH", "SOL", "USDC", "USDT", "WBTC", "DAI", "stETH", "wstETH"];

interface PageProps {
  params: Promise<{ symbol: string }>;
}

function toHistoryMarkets(data: Awaited<ReturnType<typeof fetchAssetHistory>>): LendingHistoryMarket[] {
  if (!data) return [];
  return data.lending.map((m) => ({
    protocol: m.protocol,
    market: m.market,
    chain: m.chain,
    data: m.data,
  }));
}

export default async function AssetPage({ params }: PageProps) {
  const { symbol } = await params;
  const sym = symbol.toUpperCase();

  // Parallel fetch — all gracefully return empty on error
  const [lending, derivatives, staking, history, riskParams, borrowDemand] = await Promise.all([
    fetchAssetLending(sym),
    fetchAssetDerivatives(sym),
    fetchAssetStaking(sym),
    fetchAssetHistory(sym, 30),
    fetchLtvMatrix([sym]),
    fetchBorrowDemand(sym, 30),
  ]);

  // If no data at all and symbol not in our tracked list, treat as 404
  if (
    !VALID_SYMBOLS.includes(sym) &&
    !lending &&
    derivatives.length === 0
  ) {
    notFound();
  }

  const lendingMarkets = lending?.markets ?? [];
  const historyMarkets = toHistoryMarkets(history);

  const markPrice = derivatives[0]?.mark_price ?? null;
  const latestSnapshot = derivatives[0]?.snapshot_at;

  return (
    <div className="asset-page">
      {/* ── Header ─────────────────────────────────────────────── */}
      <header className="asset-header">
        <div className="asset-header-left">
          <h1 className="asset-symbol">{sym}</h1>
          {markPrice && (
            <span className="asset-mark-price">
              ${markPrice.toLocaleString("en-US", { maximumFractionDigits: 2 })}
            </span>
          )}
        </div>
        <div className="asset-header-right">
          <SourceTag source="Velo" />
          <SourceTag source="DeFiLlama" />
          {riskParams.length > 0 && <SourceTag source="Aave / Morpho / Kamino" />}
          {latestSnapshot && (
            <span className="asset-snapshot-ts">
              {new Date(latestSnapshot).toLocaleTimeString()}
            </span>
          )}
        </div>
      </header>

      {/* ── Lending markets ────────────────────────────────────── */}
      <SectionCard
        title="Lending Markets"
        source="DeFiLlama"
        empty={lendingMarkets.length === 0}
        emptyMessage="No lending data available"
      >
        <LendingTable rows={lendingMarkets} />
      </SectionCard>

      {/* ── Derivatives ────────────────────────────────────────── */}
      <SectionCard
        title="Derivatives"
        source="Velo"
        empty={derivatives.length === 0}
        emptyMessage="No derivatives data available"
      >
        <DerivativesTable rows={derivatives} />
      </SectionCard>

      {/* ── Staking ────────────────────────────────────────────── */}
      {staking.length > 0 && (
        <SectionCard title="Staking / LSD" source="DeFiLlama">
          <StakingTable rows={staking} />
        </SectionCard>
      )}

      {/* ── Risk params (LTV matrix) ────────────────────────────── */}
      <SectionCard
        title="Protocol Risk Params"
        source="Aave · Morpho · Kamino"
        empty={riskParams.length === 0}
        emptyMessage="No risk param data — run ingest_all() first"
      >
        <RiskParamsTable rows={riskParams} />
      </SectionCard>

      {/* ── History charts ─────────────────────────────────────── */}
      <SectionCard
        title="History (30d)"
        source="DeFiLlama"
        empty={historyMarkets.length === 0}
        emptyMessage="No historical data available"
      >
        <HistoryChart
          markets={historyMarkets}
          metric="supply_apy"
          title="Supply APY (%)"
        />
        <HistoryChart
          markets={historyMarkets}
          metric="borrow_apy"
          title="Borrow APY (%)"
        />
      </SectionCard>

      {/* ── Borrow demand explainer ─────────────────────────────── */}
      <SectionCard
        title="Why Is Borrow Demand Elevated?"
        source="Velo · DeFiLlama · internal engine"
      >
        <BorrowDemandCard analysis={borrowDemand} symbol={sym} />
      </SectionCard>

      {/* ── Transforms (placeholder) ───────────────────────────── */}
      <SectionCard title="Transforms">
        <TransformsSection symbol={sym} />
      </SectionCard>

      {/* ── Events (placeholder) ────────────────────────────────── */}
      <SectionCard title="Events">
        <EventsSection symbol={sym} />
      </SectionCard>
    </div>
  );
}
