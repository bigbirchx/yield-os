interface FreshnessTagProps {
  isoTimestamp: string;
}

function relativeTime(isoTimestamp: string): { label: string; stale: boolean } {
  const diff = Date.now() - new Date(isoTimestamp).getTime();
  const mins = Math.floor(diff / 60_000);
  const stale = mins > 30;

  if (mins < 1) return { label: "< 1m ago", stale };
  if (mins < 60) return { label: `${mins}m ago`, stale };
  const hrs = Math.floor(mins / 60);
  return { label: `${hrs}h ago`, stale: true };
}

export function FreshnessTag({ isoTimestamp }: FreshnessTagProps) {
  const { label, stale } = relativeTime(isoTimestamp);
  return (
    <span className={`freshness-tag${stale ? " stale" : ""}`} title={isoTimestamp}>
      {label}
    </span>
  );
}
