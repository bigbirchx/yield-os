import { FreshnessTag } from "@/components/overview/FreshnessTag";
import type { BorrowDemandAnalysis, ReasonFactor } from "@/types/api";

interface BorrowDemandCardProps {
  analysis: BorrowDemandAnalysis | null;
  symbol: string;
}

const DEMAND_CONFIG: Record<
  BorrowDemandAnalysis["demand_level"],
  { label: string; cls: string }
> = {
  elevated:   { label: "⬆ ELEVATED",   cls: "demand-elevated" },
  normal:     { label: "— NORMAL",      cls: "demand-normal" },
  suppressed: { label: "⬇ SUPPRESSED",  cls: "demand-suppressed" },
};

function confidenceBar(score: number) {
  const pct = Math.round(score * 100);
  const cls =
    pct >= 70 ? "conf-high" : pct >= 40 ? "conf-medium" : "conf-low";
  return (
    <div className="conf-bar-wrap" title={`Confidence: ${pct}%`}>
      <div className={`conf-bar-fill ${cls}`} style={{ width: `${pct}%` }} />
    </div>
  );
}

function factorBar(factor: ReasonFactor) {
  const pct = Math.round(factor.score * 100);
  const cls =
    factor.direction === "elevates"
      ? "factor-bar-elevates"
      : factor.direction === "suppresses"
      ? "factor-bar-suppresses"
      : "factor-bar-neutral";

  return (
    <div className="factor-row" key={factor.name}>
      <div className="factor-header">
        <span className="factor-label">{factor.display_label}</span>
        <div className="factor-meta">
          <span className="factor-source">{factor.metric_source}</span>
          {factor.snapshot_at && (
            <FreshnessTag isoTimestamp={factor.snapshot_at} />
          )}
        </div>
      </div>
      <div className="factor-values">
        <span className={`factor-direction ${cls}`}>
          {factor.direction === "elevates"
            ? "↑"
            : factor.direction === "suppresses"
            ? "↓"
            : "—"}
        </span>
        <span className="factor-value">
          {factor.value != null
            ? `${factor.value.toFixed(2)} ${factor.value_unit}`
            : "—"}
        </span>
        {factor.baseline != null && (
          <span className="factor-baseline">
            median {factor.baseline.toFixed(2)} {factor.value_unit}
          </span>
        )}
        <div className="factor-score-bar" title={`Score: ${pct}%`}>
          <div className={`factor-score-fill ${cls}`} style={{ width: `${pct}%` }} />
        </div>
      </div>
      <p className="factor-note">{factor.evidence_note}</p>
    </div>
  );
}

export function BorrowDemandCard({ analysis, symbol }: BorrowDemandCardProps) {
  if (!analysis) {
    return (
      <div className="borrow-demand-card">
        <p className="borrow-demand-no-data">
          No data — run derivatives and lending ingestion first.
        </p>
      </div>
    );
  }

  const cfg = DEMAND_CONFIG[analysis.demand_level];
  const scorePct = Math.round(Math.max(0, analysis.demand_score) * 100);

  return (
    <div className="borrow-demand-card">
      {/* ── Status strip ─────────────────────────────────────── */}
      <div className="demand-status-strip">
        <span className={`demand-badge ${cfg.cls}`}>{cfg.label}</span>
        <div className="demand-strip-meta">
          <span className="demand-score-label">
            Score {analysis.demand_score > 0 ? "+" : ""}
            {analysis.demand_score.toFixed(3)}
          </span>
          <div className="demand-conf-wrap">
            <span className="demand-conf-label">
              Confidence {Math.round(analysis.confidence * 100)}%
            </span>
            {confidenceBar(analysis.confidence)}
          </div>
          <span className="demand-window">
            {analysis.data_window_days}d window
          </span>
          <FreshnessTag isoTimestamp={analysis.computed_at} />
        </div>
      </div>

      {/* ── Explanation ──────────────────────────────────────── */}
      <blockquote className="demand-explanation">
        {analysis.explanation}
      </blockquote>

      {/* ── Factor breakdown ─────────────────────────────────── */}
      <div className="factor-list">
        <div className="factor-list-header">
          Factor breakdown
          <span className="factor-list-hint">sorted by score</span>
        </div>
        {analysis.reasons
          .filter((f) => f.score > 0 || f.direction !== "neutral")
          .map((f) => factorBar(f))}
      </div>

      {/* ── Event overlays ───────────────────────────────────── */}
      {analysis.event_overlays.length > 0 && (
        <div className="event-overlays">
          <div className="event-overlays-header">Event overlays</div>
          {analysis.event_overlays.map((ev) => (
            <div key={ev.label} className="event-row">
              <span
                className={`event-impact-tag ${
                  ev.impact === "elevates"
                    ? "impact-elevates"
                    : ev.impact === "suppresses"
                    ? "impact-suppresses"
                    : "impact-neutral"
                }`}
              >
                {ev.impact}
              </span>
              <span className="event-label">{ev.label}</span>
              <span className="event-source">{ev.source}</span>
              {ev.notes && <span className="event-notes">{ev.notes}</span>}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
