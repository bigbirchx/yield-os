import { FreshnessTag } from "@/components/overview/FreshnessTag";
import type { OptimizedRoute, RouteOptimizerResult } from "@/types/api";

const RANK_BADGE: Record<number, string> = {
  1: "route-rank-1",
  2: "route-rank-2",
  3: "route-rank-3",
  4: "route-rank-4",
};

const ROUTE_TYPE_LABEL: Record<string, string> = {
  direct_borrow:      "Direct Borrow",
  stable_borrow_spot: "Stable → Spot",
  wrapper_transform:  "Wrapper / Transform",
  synthetic_hedge:    "Synthetic Hedge",
};

function usd(v: number) {
  if (v >= 1e9) return `$${(v / 1e9).toFixed(1)}B`;
  if (v >= 1e6) return `$${(v / 1e6).toFixed(1)}M`;
  if (v >= 1e3) return `$${(v / 1e3).toFixed(0)}K`;
  return `$${v.toFixed(0)}`;
}

function costBar(bps: number, maxBps: number) {
  const ratio = Math.min(bps / maxBps, 1.0);
  const cls =
    bps < 100 ? "cost-bar-low" : bps < 500 ? "cost-bar-mid" : "cost-bar-high";
  return (
    <div className="cost-bar-track">
      <div
        className={`cost-bar-fill ${cls}`}
        style={{ width: `${Math.max(ratio * 100, 1)}%` }}
      />
    </div>
  );
}

function RouteRow({
  route,
  maxBps,
  isRecommended,
}: {
  route: OptimizedRoute;
  maxBps: number;
  isRecommended: boolean;
}) {
  const badgeCls = RANK_BADGE[route.rank] ?? "route-rank-other";

  return (
    <div className={`route-row ${isRecommended ? "route-row-recommended" : ""} ${!route.feasible ? "route-row-infeasible" : ""}`}>
      {/* Header */}
      <div className="route-row-header">
        <span className={`route-rank-badge ${badgeCls}`}>#{route.rank}</span>
        <span className="route-display-name">
          {route.display_name}
          {isRecommended && <span className="route-best-tag">BEST</span>}
        </span>
        {!route.feasible && (
          <span className="route-infeasible-tag">N/A</span>
        )}
        <div className="route-row-right">
          <span className="route-cost">
            {route.feasible ? `${route.total_cost_bps.toFixed(0)} bps ann.` : "—"}
          </span>
          <span className="route-size">
            {route.feasible ? `max ${usd(route.max_executable_usd)}` : "—"}
          </span>
        </div>
      </div>

      {/* Cost bar */}
      {route.feasible && costBar(route.total_cost_bps, maxBps)}

      {/* Description */}
      <p className="route-description">{route.description}</p>

      {/* Cost components */}
      {route.cost_components.length > 0 && (
        <div className="route-cost-components">
          {route.cost_components.map((c) => (
            <div key={c.name} className="cost-comp-row">
              <span className="cost-comp-name">{c.name.replace(/_/g, " ")}</span>
              <span
                className={`cost-comp-value ${
                  c.value_bps < 0 ? "cost-comp-income" : "cost-comp-cost"
                }`}
              >
                {c.value_bps >= 0 ? "+" : ""}
                {c.value_bps.toFixed(0)} bps
              </span>
              <span className={`cost-comp-source ${c.is_assumption ? "is-assumption" : ""}`}>
                {c.is_assumption ? "assumed" : c.source}
              </span>
            </div>
          ))}
        </div>
      )}

      {/* Bottlenecks */}
      {route.bottlenecks.length > 0 && (
        <div className="route-bottlenecks">
          {route.bottlenecks.map((b) => (
            <div
              key={b.constraint}
              className={`bottleneck-row ${b.severity === "hard" ? "bottleneck-hard" : "bottleneck-soft"}`}
            >
              <span className={`bottleneck-badge ${b.severity === "hard" ? "badge-hard" : "badge-soft"}`}>
                {b.severity}
              </span>
              <span className="bottleneck-text">{b.limiting_factor}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export function RouteOptimizerCard({
  result,
}: {
  result: RouteOptimizerResult | null;
}) {
  if (!result) {
    return (
      <div className="route-optimizer-card">
        <p className="route-no-data">
          No data — run lending and derivatives ingestion first.
        </p>
      </div>
    );
  }

  const feasible = result.routes.filter((r) => r.feasible);
  const maxBps = feasible.length > 0
    ? Math.max(...feasible.map((r) => r.total_cost_bps), 1)
    : 1;

  return (
    <div className="route-optimizer-card">
      {/* Summary strip */}
      <div className="route-summary-strip">
        <div className="route-summary-meta">
          <span className="route-request-size">
            Request: {usd(result.request_size_usd)}
          </span>
          <FreshnessTag isoTimestamp={result.computed_at} />
        </div>
        <p className="route-summary-text">{result.summary}</p>
      </div>

      {/* Route rows */}
      <div className="route-list">
        {result.routes.map((route) => (
          <RouteRow
            key={route.route_type}
            route={route}
            maxBps={maxBps}
            isRecommended={route.route_type === result.recommended_route}
          />
        ))}
      </div>

      {/* Assumptions panel */}
      <details className="assumptions-panel">
        <summary className="assumptions-summary">
          Assumptions <span className="assumptions-hint">(click to expand)</span>
        </summary>
        <div className="assumptions-grid">
          {[
            ["Max pool share", `${(result.assumptions.max_pool_share * 100).toFixed(0)}%`],
            ["Max OI share", `${(result.assumptions.max_oi_share * 100).toFixed(0)}%`],
            ["Spot slippage", `${result.assumptions.spot_slippage_bps} bps`],
            ["Funding variance premium", `${result.assumptions.funding_variance_premium_bps} bps`],
            ["Wrapper extra slippage", `${result.assumptions.wrapper_extra_slippage_bps} bps`],
            ["Unbonding premium", `${result.assumptions.unbonding_bps_per_day} bps/day`],
          ].map(([label, val]) => (
            <div key={label} className="assumption-item">
              <span className="assumption-label">{label}</span>
              <span className="assumption-value">{val}</span>
            </div>
          ))}
        </div>
      </details>
    </div>
  );
}
