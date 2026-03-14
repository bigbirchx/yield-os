"use client";

/**
 * SourceStatusPanel
 *
 * A compact header cluster showing the live health of every data source.
 * Click the cluster to open a floating panel; hover any row to see a
 * tooltip listing exactly what that source populates in the UI.
 *
 *   ● ● ○ ●  SOURCES ▾
 *   └─ expands to a panel:
 *        ● DeFiLlama    fresh  •  44 791 rows  •  5 min ago
 *        ● Aave v3      fresh  •    360 rows   •  7 min ago
 *        ○ Velo         missing — no data yet
 *        ...
 */

import { useCallback, useEffect, useRef, useState } from "react";
import type { SourceStatus } from "@/types/api";
import { fetchSources } from "@/lib/api";

// How often to re-poll (ms). Matches the DataRefresher 5-min session guard.
const POLL_INTERVAL_MS = 60_000; // 1 min — lightweight, just a single SELECT

// -------------------------------------------------------------------------
// Small helpers
// -------------------------------------------------------------------------

function relativeTime(iso: string | null): string {
  if (!iso) return "—";
  const diffMs = Date.now() - new Date(iso).getTime();
  const mins = Math.floor(diffMs / 60_000);
  if (mins < 1) return "< 1 min ago";
  if (mins < 60) return `${mins} min ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs} hr ago`;
  return `${Math.floor(hrs / 24)} d ago`;
}

function statusColor(s: SourceStatus["status"]): string {
  return s === "fresh" ? "var(--green)" : s === "stale" ? "var(--yellow)" : "var(--text-muted)";
}

function statusLabel(s: SourceStatus["status"]): string {
  return s === "fresh" ? "fresh" : s === "stale" ? "stale" : "missing";
}

function worstStatus(sources: SourceStatus[]): SourceStatus["status"] {
  if (sources.some((s) => s.status === "missing")) return "missing";
  if (sources.some((s) => s.status === "stale")) return "stale";
  return "fresh";
}

// -------------------------------------------------------------------------
// Dot component
// -------------------------------------------------------------------------

function Dot({ status, size = 7 }: { status: SourceStatus["status"]; size?: number }) {
  return (
    <span
      style={{
        display: "inline-block",
        width: size,
        height: size,
        borderRadius: "50%",
        background: statusColor(status),
        flexShrink: 0,
        opacity: status === "missing" ? 0.45 : 1,
      }}
    />
  );
}

// -------------------------------------------------------------------------
// Row tooltip
// -------------------------------------------------------------------------

function PopulatesTooltip({ items }: { items: string[] }) {
  return (
    <span className="src-populates-tooltip">
      <span className="src-populates-heading">Populates</span>
      <ul>
        {items.map((item) => (
          <li key={item}>{item}</li>
        ))}
      </ul>
    </span>
  );
}

// -------------------------------------------------------------------------
// Main component
// -------------------------------------------------------------------------

export default function SourceStatusPanel() {
  const [sources, setSources] = useState<SourceStatus[]>([]);
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(true);
  const panelRef = useRef<HTMLDivElement>(null);

  const load = useCallback(async () => {
    const data = await fetchSources();
    setSources(data);
    setLoading(false);
  }, []);

  // Initial load + polling
  useEffect(() => {
    load();
    const id = setInterval(load, POLL_INTERVAL_MS);
    return () => clearInterval(id);
  }, [load]);

  // Close on outside click
  useEffect(() => {
    if (!open) return;
    function handle(e: MouseEvent) {
      if (panelRef.current && !panelRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", handle);
    return () => document.removeEventListener("mousedown", handle);
  }, [open]);

  const worst = loading ? "missing" : worstStatus(sources);

  return (
    <div className="src-panel-root" ref={panelRef}>
      {/* ── Trigger button ────────────────────────────────── */}
      <button
        className="src-trigger"
        onClick={() => setOpen((v) => !v)}
        aria-label="Data sources status"
        title="Data source health"
      >
        {/* Mini dot cluster */}
        <span className="src-dot-cluster">
          {loading ? (
            <span className="src-trigger-spinner" />
          ) : (
            sources.slice(0, 5).map((s) => (
              <Dot key={s.key} status={s.status} size={6} />
            ))
          )}
        </span>
        <span className="src-trigger-label">SOURCES</span>
        <span className="src-trigger-caret" style={{ opacity: 0.5 }}>
          {open ? "▴" : "▾"}
        </span>
      </button>

      {/* ── Dropdown panel ──────────────────────────────── */}
      {open && (
        <div className="src-panel">
          <div className="src-panel-header">
            <span>Data Sources</span>
            <Dot status={worst} size={8} />
          </div>

          <div className="src-panel-body">
            {loading && (
              <p className="src-empty">Loading…</p>
            )}
            {!loading && sources.length === 0 && (
              <p className="src-empty">API unreachable</p>
            )}
            {sources.map((src) => (
              <div key={src.key} className="src-row">
                {/* Status dot */}
                <Dot status={src.status} size={7} />

                {/* Label + meta */}
                <div className="src-row-main">
                  <span className="src-row-label">{src.label}</span>
                  <span
                    className="src-row-status"
                    style={{ color: statusColor(src.status) }}
                  >
                    {statusLabel(src.status)}
                  </span>
                </div>

                {/* Right: row count + age */}
                <div className="src-row-meta">
                  {src.row_count > 0 && (
                    <span className="src-row-count">
                      {src.row_count.toLocaleString()} rows
                    </span>
                  )}
                  <span className="src-row-age">
                    {relativeTime(src.last_updated)}
                  </span>
                </div>

                {/* Hover tooltip */}
                <PopulatesTooltip items={src.populates} />
              </div>
            ))}
          </div>

          <div className="src-panel-footer">
            Hover a source to see what it populates
          </div>
        </div>
      )}
    </div>
  );
}
