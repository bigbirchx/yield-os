/**
 * Small summary statistic card with optional change indicator.
 */
import { changeColor, formatChange } from "@/lib/theme";

interface StatCardProps {
  title: string;
  value: string;
  /** Change value (e.g. +5.23 for +5.23%) */
  change?: number | null;
  /** Change suffix (default "%" — appended to formatChange output) */
  changeSuffix?: string;
  /** Optional subtitle / secondary text */
  subtitle?: string;
}

export default function StatCard({
  title,
  value,
  change,
  subtitle,
}: StatCardProps) {
  return (
    <div className="sc-root">
      <div className="sc-title">{title}</div>
      <div className="sc-value">{value}</div>
      <div className="sc-footer">
        {change != null && (
          <span className="sc-change" style={{ color: changeColor(change) }}>
            {change > 0 ? "\u25B2" : change < 0 ? "\u25BC" : ""}{" "}
            {formatChange(change)}
          </span>
        )}
        {subtitle && <span className="sc-subtitle">{subtitle}</span>}
      </div>
    </div>
  );
}
