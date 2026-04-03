/**
 * Progress bar showing capacity utilization.
 *
 * Color coded: green >50% remaining, amber 10-50%, red <10%.
 */
import { formatUSD } from "@/lib/theme";

interface CapacityBarProps {
  current: number;
  cap: number;
  /** Show numeric label (default true) */
  showLabel?: boolean;
  /** Height in px (default 6) */
  height?: number;
}

export default function CapacityBar({
  current,
  cap,
  showLabel = true,
  height = 6,
}: CapacityBarProps) {
  if (cap <= 0) return null;

  const used = Math.min(current / cap, 1);
  const remaining = 1 - used;
  const pct = Math.round(used * 100);

  let color: string;
  if (remaining > 0.5) color = "var(--green)";
  else if (remaining > 0.1) color = "var(--yellow)";
  else color = "var(--red)";

  return (
    <div className="cb-root">
      <div className="cb-track" style={{ height }}>
        <div
          className="cb-fill"
          style={{ width: `${pct}%`, backgroundColor: color, height }}
        />
      </div>
      {showLabel && (
        <span className="cb-label">
          {formatUSD(current)} / {formatUSD(cap)} ({pct}%)
        </span>
      )}
    </div>
  );
}
