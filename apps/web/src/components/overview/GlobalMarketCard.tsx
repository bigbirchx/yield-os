/**
 * GlobalMarketCard
 *
 * Server component — displays CoinGecko global crypto market context.
 * Rendered at the top of the overview page as a lightweight market pulse.
 */

import type { GlobalMarket } from "@/types/api";

function fmtUsd(v: number | null | undefined): string {
  if (v == null) return "—";
  if (v >= 1e12) return `$${(v / 1e12).toFixed(2)}T`;
  if (v >= 1e9) return `$${(v / 1e9).toFixed(1)}B`;
  return `$${v.toLocaleString()}`;
}

function fmtPct(v: number | null | undefined): string {
  if (v == null) return "—";
  return `${v >= 0 ? "+" : ""}${v.toFixed(2)}%`;
}

interface Props {
  data: GlobalMarket | null;
}

export function GlobalMarketCard({ data }: Props) {
  if (!data) return null;

  const changeColor =
    (data.market_cap_change_24h_pct ?? 0) >= 0 ? "var(--green)" : "var(--red)";

  const items = [
    { label: "Total Market Cap", value: fmtUsd(data.total_market_cap_usd) },
    {
      label: "24h Change",
      value: fmtPct(data.market_cap_change_24h_pct),
      color: changeColor,
    },
    { label: "24h Volume", value: fmtUsd(data.total_volume_24h_usd) },
    {
      label: "BTC Dominance",
      value: data.btc_dominance_pct != null ? `${data.btc_dominance_pct.toFixed(1)}%` : "—",
    },
    {
      label: "ETH Dominance",
      value: data.eth_dominance_pct != null ? `${data.eth_dominance_pct.toFixed(1)}%` : "—",
    },
    {
      label: "Active Assets",
      value: data.active_cryptocurrencies?.toLocaleString() ?? "—",
    },
  ];

  return (
    <div className="global-mkt-card card">
      <div className="card-header">
        Global Market
        <span className="src-tag">CoinGecko</span>
      </div>
      <div className="global-mkt-grid">
        {items.map(({ label, value, color }) => (
          <div key={label} className="global-mkt-item">
            <span className="global-mkt-label">{label}</span>
            <span
              className="global-mkt-value"
              style={color ? { color } : undefined}
            >
              {value}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}
