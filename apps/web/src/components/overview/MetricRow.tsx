import { FreshnessTag } from "./FreshnessTag";

export interface MetricRowData {
  rank: number;
  asset: string;
  subLabel: string;
  chain: string | null;
  value: string;
  valueSub?: string;
  valueColor: "green" | "red" | "yellow" | "orange";
  snapshotAt: string;
  href?: string;
}

interface MetricRowProps {
  row: MetricRowData;
}

export function MetricRow({ row }: MetricRowProps) {
  const assetEl = (
    <span className="metric-label-asset">
      {row.asset}
      {row.chain && (
        <span className="metric-label-chain">{row.chain}</span>
      )}
    </span>
  );

  return (
    <div className="metric-row">
      <span className="metric-rank">{row.rank}</span>
      <div className="metric-label">
        {row.href ? (
          <a href={row.href} style={{ textDecoration: "none", color: "inherit" }}>
            {assetEl}
          </a>
        ) : (
          assetEl
        )}
        <span className="metric-label-sub">
          {row.subLabel}{" "}
          <FreshnessTag isoTimestamp={row.snapshotAt} />
        </span>
      </div>
      <div style={{ textAlign: "right" }}>
        <div className={`metric-value ${row.valueColor}`}>{row.value}</div>
        {row.valueSub && (
          <div className="metric-value-sub">{row.valueSub}</div>
        )}
      </div>
    </div>
  );
}
