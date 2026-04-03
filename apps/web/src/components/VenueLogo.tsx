/**
 * Venue icon / badge component.
 *
 * Renders a small colored dot + venue name. Uses brand colors from the theme.
 * Falls back gracefully for unknown venues.
 */
import { venueColors, venueLabels } from "@/lib/theme";

interface VenueLogoProps {
  venue: string;
  /** Show text label (default true) */
  showLabel?: boolean;
  /** Size variant */
  size?: "sm" | "md";
}

export default function VenueLogo({
  venue,
  showLabel = true,
  size = "md",
}: VenueLogoProps) {
  const color = venueColors[venue] ?? "#94a3b8";
  const label = venueLabels[venue] ?? venue;

  return (
    <span className={`vl-root vl-${size}`}>
      <span
        className="vl-dot"
        style={{ backgroundColor: color }}
      />
      {showLabel && <span className="vl-name">{label}</span>}
    </span>
  );
}
