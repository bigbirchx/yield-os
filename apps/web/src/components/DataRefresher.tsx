"use client";

/**
 * DataRefresher — fires a background data refresh once per browser session.
 *
 * On first mount (i.e. when a user opens or reloads the app) it calls
 * POST /api/admin/ingest to pull the latest data from all connectors into
 * the database, then calls router.refresh() so every Server Component on
 * the current page re-renders against the freshly populated DB.
 *
 * sessionStorage is used as a guard so navigating between pages during the
 * same session does not trigger redundant ingest calls.
 *
 * Status is surfaced as a small badge rendered in the header.
 */

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { triggerIngest } from "@/lib/api";

type Status = "idle" | "refreshing" | "done" | "error";

const SESSION_KEY = "yield_cockpit_refreshed_at";
// Minimum milliseconds between automatic refreshes for the same session.
const MIN_REFRESH_INTERVAL_MS = 5 * 60 * 1000; // 5 min

export default function DataRefresher() {
  const router = useRouter();
  const [status, setStatus] = useState<Status>("idle");
  const [freshAt, setFreshAt] = useState<string | null>(null);

  useEffect(() => {
    const lastRefreshed = sessionStorage.getItem(SESSION_KEY);
    const now = Date.now();

    if (lastRefreshed && now - Number(lastRefreshed) < MIN_REFRESH_INTERVAL_MS) {
      // Already refreshed recently in this session — just show the timestamp.
      setFreshAt(new Date(Number(lastRefreshed)).toLocaleTimeString());
      setStatus("done");
      return;
    }

    let cancelled = false;

    async function refresh() {
      setStatus("refreshing");
      const result = await triggerIngest();
      if (cancelled) return;

      if (result !== null) {
        const ts = Date.now();
        sessionStorage.setItem(SESSION_KEY, String(ts));
        setFreshAt(new Date(ts).toLocaleTimeString());
        setStatus("done");
        // Re-render all Server Components with fresh DB data.
        router.refresh();
      } else {
        setStatus("error");
      }
    }

    refresh();
    return () => {
      cancelled = true;
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (status === "idle") return null;

  return (
    <span
      className="data-refresher-badge"
      title={
        status === "refreshing"
          ? "Pulling latest data from all sources…"
          : status === "done"
          ? `Data refreshed at ${freshAt}`
          : "Data refresh failed — showing last cached values"
      }
    >
      {status === "refreshing" && (
        <>
          <span className="data-refresher-spinner" aria-hidden="true" />
          Refreshing…
        </>
      )}
      {status === "done" && <>&#10003; Fresh {freshAt}</>}
      {status === "error" && <>&#9888; Refresh failed</>}
    </span>
  );
}
