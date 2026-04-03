import { fetchOpportunities, fetchOpportunitySummary } from "@/lib/api";
import Link from "next/link";
import { formatAPY, formatUSD, getUmbrellaColor } from "@/lib/theme";
import type { MarketOpportunity } from "@/types/api";

export const metadata = { title: "Assets | Yield Cockpit" };

const UMBRELLA_META: Record<string, { label: string; description: string }> = {
  USD: {
    label: "US Dollar",
    description: "Stablecoins, savings rates, and tokenized yield strategies",
  },
  ETH: {
    label: "Ethereum",
    description: "ETH, liquid staking tokens (stETH, rETH, cbETH) and LRTs",
  },
  BTC: {
    label: "Bitcoin",
    description: "BTC, WBTC, CBBTC, and wrapped variants",
  },
  SOL: {
    label: "Solana",
    description: "SOL and liquid staking tokens (mSOL, jitoSOL, bSOL)",
  },
  HYPE: {
    label: "Hyperliquid",
    description: "HYPE native token and ecosystem opportunities",
  },
  OTHER: {
    label: "Other Assets",
    description: "Miscellaneous tracked assets",
  },
};

const UMBRELLA_ORDER = ["USD", "ETH", "BTC", "SOL", "HYPE", "OTHER"];

interface UmbrellaStats {
  topApy: number;
  tvl: number;
  supplyCount: number;
  borrowCount: number;
}

function computeStats(opps: MarketOpportunity[]): Record<string, UmbrellaStats> {
  const stats: Record<string, UmbrellaStats> = {};
  for (const opp of opps) {
    const u = opp.umbrella_group;
    if (!stats[u]) stats[u] = { topApy: 0, tvl: 0, supplyCount: 0, borrowCount: 0 };
    if (opp.side === "SUPPLY") {
      stats[u].supplyCount++;
      stats[u].tvl += opp.tvl_usd ?? 0;
      if (opp.total_apy_pct > stats[u].topApy) stats[u].topApy = opp.total_apy_pct;
    } else {
      stats[u].borrowCount++;
    }
  }
  return stats;
}

export default async function AssetsIndexPage() {
  const [allOpps, summary] = await Promise.all([
    fetchOpportunities({ limit: 500, exclude_amm_lp: true, exclude_pendle: true }),
    fetchOpportunitySummary(),
  ]);

  const stats = computeStats(allOpps.data);

  return (
    <div className="asset-page">
      <header className="asset-header">
        <div className="asset-header-left">
          <h1 className="asset-symbol">Asset Cockpits</h1>
        </div>
        <div className="asset-header-right">
          <span className="asset-snapshot-ts">
            {summary?.total_opportunities ?? 0} total opportunities
          </span>
        </div>
      </header>

      <p style={{ fontSize: "12px", color: "var(--text-muted)", marginTop: "-0.5rem" }}>
        Deep-dive analytics for each asset umbrella group. Click a card to explore
        supply and borrow rates, historical yields, and conversion paths.
      </p>

      <div className="umbrella-grid">
        {UMBRELLA_ORDER.map((umbrella) => {
          const meta = UMBRELLA_META[umbrella];
          const s = stats[umbrella] ?? { topApy: 0, tvl: 0, supplyCount: 0, borrowCount: 0 };
          const totalCount = (summary?.by_umbrella?.[umbrella] ?? s.supplyCount + s.borrowCount);
          const color = getUmbrellaColor(umbrella);

          return (
            <Link key={umbrella} href={`/assets/${umbrella}`} className="umbrella-card-link">
              <div className="umbrella-card" style={{ "--umb-color": color } as React.CSSProperties}>
                <div className="umbrella-card-head">
                  <div className="umbrella-card-name">
                    <span className="umbrella-dot" style={{ background: color }} />
                    <span className="umbrella-symbol">{umbrella}</span>
                    <span className="umbrella-label">{meta.label}</span>
                  </div>
                  <span className="umbrella-count">{totalCount} opps</span>
                </div>

                <p className="umbrella-desc">{meta.description}</p>

                <div className="umbrella-stats">
                  <div className="umbrella-stat">
                    <span className="umbrella-stat-label">Top Supply APY</span>
                    <span
                      className="umbrella-stat-value"
                      style={{ color: s.topApy > 0 ? "var(--green)" : "var(--text-muted)" }}
                    >
                      {s.topApy > 0 ? formatAPY(s.topApy) : "--"}
                    </span>
                  </div>
                  <div className="umbrella-stat">
                    <span className="umbrella-stat-label">Supply TVL</span>
                    <span className="umbrella-stat-value">{s.tvl > 0 ? formatUSD(s.tvl) : "--"}</span>
                  </div>
                  <div className="umbrella-stat">
                    <span className="umbrella-stat-label">Supply / Borrow</span>
                    <span className="umbrella-stat-value">
                      {s.supplyCount} / {s.borrowCount}
                    </span>
                  </div>
                </div>
              </div>
            </Link>
          );
        })}
      </div>
    </div>
  );
}
