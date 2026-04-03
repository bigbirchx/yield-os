"use client";
/**
 * Top bar with system health indicator, last refresh timestamp, dark/light toggle.
 */
import Link from "next/link";
import { useHealth, useWorkerHealth } from "@/lib/hooks";
import SourceStatusPanel from "@/components/SourceStatusPanel";

export default function TopBar() {
  const { data: health } = useHealth();
  const { data: workerHealth } = useWorkerHealth();

  const workerStatus = workerHealth?.worker_status ?? health?.worker_status ?? "unknown";

  const healthDot =
    workerStatus === "healthy"
      ? "tb-dot-green"
      : workerStatus === "degraded"
        ? "tb-dot-amber"
        : "tb-dot-red";

  const lastRefresh = workerHealth?.last_heartbeat
    ? new Date(workerHealth.last_heartbeat).toLocaleTimeString("en-US", {
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
      })
    : null;

  return (
    <header className="tb-root">
      <Link href="/" className="tb-logo">
        YIELD COCKPIT
      </Link>

      <div className="tb-spacer" />

      {/* System health indicator */}
      <div className="tb-health" title={`Worker: ${workerStatus}`}>
        <span className={`tb-dot ${healthDot}`} />
        <span className="tb-health-label">
          {workerStatus === "healthy" ? "Live" : workerStatus}
        </span>
      </div>

      {/* Last refresh timestamp */}
      {lastRefresh && (
        <span className="tb-refresh">
          Last update: {lastRefresh}
        </span>
      )}

      {/* Source status dropdown (existing component) */}
      <SourceStatusPanel />
    </header>
  );
}
