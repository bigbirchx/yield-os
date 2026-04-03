/**
 * Inline asset badge showing symbol + umbrella color + optional sub-type tag.
 *
 * Works as both a server and client component (no hooks or state).
 */
import { getUmbrellaColor, subTypeLabels } from "@/lib/theme";

interface AssetBadgeProps {
  symbol: string;
  umbrella?: string;
  subType?: string;
  /** Show the sub-type tag (default true) */
  showTag?: boolean;
  /** Size variant */
  size?: "sm" | "md";
}

export default function AssetBadge({
  symbol,
  umbrella = "OTHER",
  subType,
  showTag = true,
  size = "md",
}: AssetBadgeProps) {
  const color = getUmbrellaColor(umbrella);
  const tagLabel = subType ? subTypeLabels[subType] ?? subType : null;

  return (
    <span className={`ab-root ab-${size}`}>
      <span className="ab-dot" style={{ backgroundColor: color }} />
      <span className="ab-symbol">{symbol}</span>
      {showTag && tagLabel && tagLabel !== "Token" && tagLabel !== "Native" && (
        <span className="ab-tag" style={{ color, borderColor: `${color}40` }}>
          {tagLabel}
        </span>
      )}
    </span>
  );
}
