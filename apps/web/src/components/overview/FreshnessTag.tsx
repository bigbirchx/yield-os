"use client";

import { useEffect, useState } from "react";

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
  // Start with null so server HTML and initial client HTML are identical,
  // then update after mount to avoid the SSR/hydration mismatch caused by
  // Date.now() producing different values on server vs client.
  const [rel, setRel] = useState<{ label: string; stale: boolean } | null>(null);

  useEffect(() => {
    setRel(relativeTime(isoTimestamp));
    const id = setInterval(() => setRel(relativeTime(isoTimestamp)), 30_000);
    return () => clearInterval(id);
  }, [isoTimestamp]);

  if (!rel) {
    // Render a stable placeholder that matches the server output exactly.
    return (
      <span className="freshness-tag" title={isoTimestamp}>
        …
      </span>
    );
  }

  return (
    <span className={`freshness-tag${rel.stale ? " stale" : ""}`} title={isoTimestamp}>
      {rel.label}
    </span>
  );
}
