import { MetricRow, MetricRowData } from "./MetricRow";
import { SourceTag } from "./SourceTag";

interface MetricSectionProps {
  title: string;
  titleColor?: "green" | "red" | "yellow" | "orange";
  source: string;
  rows: MetricRowData[];
  emptyMessage?: string;
}

export function MetricSection({
  title,
  titleColor,
  source,
  rows,
  emptyMessage = "No data",
}: MetricSectionProps) {
  return (
    <div className="metric-section">
      <div className="metric-section-header">
        <span className={`metric-section-title${titleColor ? ` ${titleColor}` : ""}`}>
          {title}
        </span>
        <SourceTag source={source} />
      </div>
      <div className="metric-section-body">
        {rows.length === 0 ? (
          <div className="metric-empty">{emptyMessage}</div>
        ) : (
          rows.map((row) => <MetricRow key={row.rank} row={row} />)
        )}
      </div>
    </div>
  );
}
