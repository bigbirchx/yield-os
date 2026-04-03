"use client";
/**
 * Route Optimizer — interactive yield route finder.
 *
 * Two tabs:
 *  1. Find Routes — single-asset optimizer with full route breakdown
 *  2. Compare Assets — side-by-side comparison across entry assets
 */
import { useCallback, useState } from "react";
import {
  fetchOptimizedRoutes,
  fetchOptimizerCompare,
} from "@/lib/api";
import { formatAPY, formatUSD, apyColor } from "@/lib/theme";
import type {
  OptimizerRequestConfig,
  OptimizerResponse,
  OptimizerCompareResponse,
  YieldRouteResult,
} from "@/types/api";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const ASSETS = ["ETH", "USDC", "USDT", "WBTC", "SOL", "DAI", "WETH", "wstETH", "cbETH", "rETH"];
const HOLDING_PERIODS = [
  { label: "7d", days: 7 },
  { label: "30d", days: 30 },
  { label: "90d", days: 90 },
  { label: "180d", days: 180 },
  { label: "1y", days: 365 },
];
const RISK_LEVELS = ["conservative", "moderate", "aggressive"] as const;
const CHAINS = ["ETHEREUM", "ARBITRUM", "BASE", "OPTIMISM", "POLYGON", "SOLANA"];

const RISK_FLAG_LABELS: Record<string, { label: string; color: string }> = {
  LOW_TVL: { label: "Low TVL", color: "var(--yellow)" },
  HIGH_UTILIZATION: { label: "High Util", color: "var(--orange)" },
  CAPACITY_CAPPED: { label: "Capped", color: "var(--yellow)" },
  WITHDRAWAL_QUEUE: { label: "Queue", color: "var(--orange)" },
  LOCKUP: { label: "Lockup", color: "var(--red)" },
  NON_DETERMINISTIC_CONVERSION: { label: "Non-determ.", color: "var(--yellow)" },
  HIGH_CONVERSION_COST: { label: "High Cost", color: "var(--orange)" },
  RATE_MODEL_KINK: { label: "Near Kink", color: "var(--red)" },
  LOW_LIQUIDITY: { label: "Low Liq", color: "var(--red)" },
  STALE_DATA: { label: "Stale", color: "var(--text-muted)" },
  ISOLATED_COLLATERAL: { label: "Isolated", color: "var(--yellow)" },
  ANOMALOUS_APY: { label: "Anomalous APY", color: "var(--red)" },
};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function fmtTime(seconds: [number, number]): string {
  const [min, max] = seconds;
  if (min === 0 && max === 0) return "Instant";
  const fmtSec = (s: number) => {
    if (s < 60) return `${s}s`;
    if (s < 3600) return `${Math.round(s / 60)}m`;
    if (s < 86400) return `${(s / 3600).toFixed(1)}h`;
    return `${(s / 86400).toFixed(1)}d`;
  };
  if (min === max) return fmtSec(min);
  return `${fmtSec(min)} – ${fmtSec(max)}`;
}

function riskColor(score: number): string {
  if (score < 0.2) return "var(--green)";
  if (score < 0.4) return "var(--yellow)";
  if (score < 0.6) return "var(--orange)";
  return "var(--red)";
}

function methodLabel(method: string): string {
  return method.replace(/_/g, " ").toLowerCase().replace(/^\w/, (c) => c.toUpperCase());
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function ConversionFlow({ route }: { route: YieldRouteResult }) {
  if (route.conversion_steps.length === 0) {
    return <span className="opt-flow-direct">Direct deposit</span>;
  }
  return (
    <div className="opt-flow">
      {route.conversion_steps.map((step, i) => (
        <span key={i} className="opt-flow-step">
          {i > 0 && <span className="opt-flow-arrow">&rarr;</span>}
          <span className="opt-flow-asset">{step.from_asset}</span>
          <span className="opt-flow-method">{methodLabel(step.method)}</span>
        </span>
      ))}
      <span className="opt-flow-arrow">&rarr;</span>
      <span className="opt-flow-asset">
        {route.conversion_steps[route.conversion_steps.length - 1].to_asset}
      </span>
    </div>
  );
}

function RiskBadges({ flags }: { flags: string[] }) {
  if (flags.length === 0) {
    return <span style={{ color: "var(--green)", fontSize: 11 }}>Low risk</span>;
  }
  return (
    <div className="opt-risk-badges">
      {flags.map((f) => {
        const cfg = RISK_FLAG_LABELS[f] ?? { label: f, color: "var(--text-muted)" };
        return (
          <span key={f} className="opt-risk-badge" style={{ borderColor: cfg.color, color: cfg.color }}>
            {cfg.label}
          </span>
        );
      })}
    </div>
  );
}

function RouteCard({ route, rank, expanded, onToggle }: {
  route: YieldRouteResult;
  rank: number;
  expanded: boolean;
  onToggle: () => void;
}) {
  const isRecommended = rank === 0;
  return (
    <div
      className={`opt-route-card ${isRecommended ? "opt-route-recommended" : ""}`}
      onClick={onToggle}
    >
      {/* Header row */}
      <div className="opt-route-header">
        <div className="opt-route-rank">
          {isRecommended ? (
            <span className="opt-recommended-badge">BEST</span>
          ) : (
            <span className="opt-rank-num">#{rank + 1}</span>
          )}
        </div>

        <div className="opt-route-identity">
          <span className="opt-route-venue">{route.venue}</span>
          <span className="opt-route-chain">{route.chain}</span>
          <span className={`opt-route-side opt-side-${route.side.toLowerCase()}`}>
            {route.side}
          </span>
          <span className="opt-route-target">{route.target_asset}</span>
          {route.market_name && (
            <span className="opt-route-market">{route.market_name}</span>
          )}
        </div>

        <div className="opt-route-metrics">
          <div className="opt-metric">
            <span className="opt-metric-label">Net APY</span>
            <span className="opt-metric-value" style={{ color: apyColor(route.net_apy_pct) }}>
              {formatAPY(route.net_apy_pct)}
            </span>
          </div>
          <div className="opt-metric">
            <span className="opt-metric-label">Gross</span>
            <span className="opt-metric-value">{formatAPY(route.gross_apy_pct)}</span>
          </div>
          <div className="opt-metric">
            <span className="opt-metric-label">Conv. Cost</span>
            <span className="opt-metric-value">{route.conversion_cost_bps.toFixed(1)} bps</span>
          </div>
          <div className="opt-metric">
            <span className="opt-metric-label">Capacity</span>
            <span className="opt-metric-value">{formatUSD(route.max_deployable_usd)}</span>
          </div>
          <div className="opt-metric">
            <span className="opt-metric-label">Risk</span>
            <span className="opt-metric-value" style={{ color: riskColor(route.risk_score) }}>
              {(route.risk_score * 100).toFixed(0)}%
            </span>
          </div>
        </div>

        <span className="opt-expand-icon">{expanded ? "\u25B2" : "\u25BC"}</span>
      </div>

      {/* Expanded detail */}
      {expanded && (
        <div className="opt-route-detail">
          {/* Flow visualization */}
          <div className="opt-detail-section">
            <div className="opt-detail-label">Conversion Path</div>
            <ConversionFlow route={route} />
            <div className="opt-detail-meta">
              Time: {fmtTime(route.conversion_time_seconds)}
              {" | "}Gas: ${route.conversion_gas_usd.toFixed(2)}
              {" | "}Ann. cost: {route.annualized_conversion_cost_pct.toFixed(4)}%
              {!route.is_conversion_deterministic && (
                <span className="opt-nondeterministic"> (non-deterministic)</span>
              )}
            </div>
          </div>

          {/* Rate impact */}
          {route.rate_impact_bps > 0 && (
            <div className="opt-detail-section">
              <div className="opt-detail-label">Rate Impact</div>
              <div className="opt-detail-meta">
                Impact: {route.rate_impact_bps.toFixed(1)} bps
                {route.post_deposit_apy_pct != null && (
                  <> | Post-deposit APY: {formatAPY(route.post_deposit_apy_pct)}</>
                )}
              </div>
            </div>
          )}

          {/* Capacity */}
          <div className="opt-detail-section">
            <div className="opt-detail-label">Capacity</div>
            <div className="opt-detail-meta">
              Max deployable: {formatUSD(route.max_deployable_usd)}
              {" | "}TVL: {formatUSD(route.tvl_usd)}
              {route.capacity_limited && (
                <span className="opt-capped-tag"> CAPPED</span>
              )}
            </div>
          </div>

          {/* Collateral (borrow routes) */}
          {route.collateral && (
            <div className="opt-detail-section">
              <div className="opt-detail-label">Collateral Required</div>
              <div className="opt-collateral-grid">
                <div>Asset: <strong>{route.collateral.collateral_asset}</strong></div>
                <div>Amount: <strong>{formatUSD(route.collateral.collateral_amount_usd)}</strong></div>
                <div>LTV: {route.collateral.max_ltv_pct.toFixed(0)}% / Liq: {route.collateral.liquidation_ltv_pct.toFixed(0)}%</div>
                <div>Buffer: {route.collateral.liquidation_buffer_pct.toFixed(1)}%</div>
                <div>Conv. cost: {route.collateral.conversion_cost_bps.toFixed(1)} bps</div>
                <div>Opp. cost: {formatAPY(route.collateral.opportunity_cost_apy_pct)}</div>
              </div>
            </div>
          )}

          {/* Risk flags */}
          <div className="opt-detail-section">
            <div className="opt-detail-label">Risk Assessment</div>
            <RiskBadges flags={route.risk_flags} />
          </div>
        </div>
      )}
    </div>
  );
}

function ResultsPanel({ result, loading }: { result: OptimizerResponse | null; loading: boolean }) {
  if (loading) {
    return (
      <div className="opt-results-empty">
        <div className="opt-spinner" />
        Computing optimal routes...
      </div>
    );
  }

  if (!result) {
    return (
      <div className="opt-results-empty">
        Configure your parameters and click &ldquo;Find Routes&rdquo; to discover optimal yield paths.
      </div>
    );
  }

  if (result.total_routes === 0) {
    return (
      <div className="opt-results-empty">
        No viable routes found for {result.entry_asset} at {formatUSD(result.entry_amount_usd)}.
        Try adjusting filters or increasing risk tolerance.
      </div>
    );
  }

  return <RouteList routes={result.routes} />;
}

function RouteList({ routes }: { routes: YieldRouteResult[] }) {
  const [expandedIdx, setExpandedIdx] = useState<number | null>(0);

  return (
    <div className="opt-route-list">
      <div className="opt-results-header">
        {routes.length} route{routes.length !== 1 ? "s" : ""} found
      </div>
      {routes.map((r, i) => (
        <RouteCard
          key={r.opportunity_id}
          route={r}
          rank={i}
          expanded={expandedIdx === i}
          onToggle={() => setExpandedIdx(expandedIdx === i ? null : i)}
        />
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Compare tab
// ---------------------------------------------------------------------------

function ComparePanel() {
  const [assetA, setAssetA] = useState("ETH");
  const [assetB, setAssetB] = useState("USDC");
  const [amount, setAmount] = useState(1_000_000);
  const [period, setPeriod] = useState(90);
  const [result, setResult] = useState<OptimizerCompareResponse | null>(null);
  const [loading, setLoading] = useState(false);

  const runCompare = useCallback(async () => {
    setLoading(true);
    try {
      const data = await fetchOptimizerCompare(
        [
          { entry_asset: assetA, entry_amount_usd: amount },
          { entry_asset: assetB, entry_amount_usd: amount },
        ],
        period,
      );
      setResult(data);
    } finally {
      setLoading(false);
    }
  }, [assetA, assetB, amount, period]);

  return (
    <div className="opt-compare">
      <div className="opt-compare-inputs">
        <div className="opt-input-group">
          <label className="opt-label">Asset A</label>
          <select className="opt-select" value={assetA} onChange={(e) => setAssetA(e.target.value)}>
            {ASSETS.map((a) => <option key={a} value={a}>{a}</option>)}
          </select>
        </div>
        <div className="opt-compare-vs">vs</div>
        <div className="opt-input-group">
          <label className="opt-label">Asset B</label>
          <select className="opt-select" value={assetB} onChange={(e) => setAssetB(e.target.value)}>
            {ASSETS.map((a) => <option key={a} value={a}>{a}</option>)}
          </select>
        </div>
        <div className="opt-input-group">
          <label className="opt-label">Amount (USD)</label>
          <input
            className="opt-input"
            type="number"
            value={amount}
            onChange={(e) => setAmount(Number(e.target.value) || 0)}
          />
        </div>
        <div className="opt-input-group">
          <label className="opt-label">Period</label>
          <div className="opt-toggle-group">
            {HOLDING_PERIODS.map((hp) => (
              <button
                key={hp.days}
                className={`opt-toggle ${period === hp.days ? "opt-toggle-active" : ""}`}
                onClick={() => setPeriod(hp.days)}
              >
                {hp.label}
              </button>
            ))}
          </div>
        </div>
        <button className="opt-btn opt-btn-primary" onClick={runCompare} disabled={loading}>
          {loading ? "Comparing..." : "Compare"}
        </button>
      </div>

      {result && result.comparisons.length === 2 && (
        <div className="opt-compare-results">
          {result.comparisons.map((comp) => (
            <div key={comp.entry_asset} className="opt-compare-col">
              <div className="opt-compare-col-header">
                <span className="opt-compare-asset">{comp.entry_asset}</span>
                <span className="opt-compare-amount">{formatUSD(comp.entry_amount_usd)}</span>
              </div>
              {comp.best_supply_route ? (
                <div className="opt-compare-best">
                  <div className="opt-compare-best-label">Best Supply Route</div>
                  <div className="opt-compare-best-apy" style={{ color: apyColor(comp.best_supply_route.net_apy_pct) }}>
                    {formatAPY(comp.best_supply_route.net_apy_pct)}
                  </div>
                  <div className="opt-compare-best-detail">
                    {comp.best_supply_route.venue} / {comp.best_supply_route.chain}
                  </div>
                  <div className="opt-compare-best-detail">
                    {comp.best_supply_route.target_asset} via{" "}
                    {comp.best_supply_route.conversion_steps.length === 0
                      ? "direct"
                      : `${comp.best_supply_route.conversion_steps.length} step(s)`}
                  </div>
                  <div className="opt-compare-best-detail">
                    Conv. cost: {comp.best_supply_route.conversion_cost_bps.toFixed(1)} bps
                  </div>
                  <RiskBadges flags={comp.best_supply_route.risk_flags} />
                </div>
              ) : (
                <div className="opt-compare-none">No supply routes found</div>
              )}
              <div className="opt-compare-total">
                {comp.total_routes} total route{comp.total_routes !== 1 ? "s" : ""}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export default function OptimizerClient() {
  const [tab, setTab] = useState<"find" | "compare">("find");

  // Find Routes state
  const [asset, setAsset] = useState("ETH");
  const [amount, setAmount] = useState(1_000_000);
  const [period, setPeriod] = useState(90);
  const [risk, setRisk] = useState<typeof RISK_LEVELS[number]>("moderate");
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [chains, setChains] = useState<string[]>([]);
  const [excludePendle, setExcludePendle] = useState(false);
  const [includeBorrow, setIncludeBorrow] = useState(true);
  const [maxSteps, setMaxSteps] = useState(3);

  const [result, setResult] = useState<OptimizerResponse | null>(null);
  const [loading, setLoading] = useState(false);

  const findRoutes = useCallback(async () => {
    setLoading(true);
    try {
      const config: OptimizerRequestConfig = {
        risk_tolerance: risk,
        exclude_pendle: excludePendle,
        include_borrow_routes: includeBorrow,
        max_conversion_steps: maxSteps,
        preferred_chains: chains.length > 0 ? chains : undefined,
      };
      const data = await fetchOptimizedRoutes(asset, amount, period, config);
      setResult(data);
    } finally {
      setLoading(false);
    }
  }, [asset, amount, period, risk, excludePendle, includeBorrow, maxSteps, chains]);

  const toggleChain = (chain: string) => {
    setChains((prev) =>
      prev.includes(chain) ? prev.filter((c) => c !== chain) : [...prev, chain]
    );
  };

  return (
    <div className="opt-page">
      <div className="opt-page-header">
        <h1 className="opt-title">Route Optimizer</h1>
        <p className="opt-subtitle">
          Find the highest net yield considering conversion costs, capacity, and rate impact
        </p>
      </div>

      {/* Tabs */}
      <div className="opt-tabs">
        <button
          className={`opt-tab ${tab === "find" ? "opt-tab-active" : ""}`}
          onClick={() => setTab("find")}
        >
          Find Routes
        </button>
        <button
          className={`opt-tab ${tab === "compare" ? "opt-tab-active" : ""}`}
          onClick={() => setTab("compare")}
        >
          Compare Assets
        </button>
      </div>

      {tab === "find" ? (
        <div className="opt-layout">
          {/* Input panel */}
          <div className="opt-inputs">
            <div className="opt-input-group">
              <label className="opt-label">Entry Asset</label>
              <select className="opt-select" value={asset} onChange={(e) => setAsset(e.target.value)}>
                {ASSETS.map((a) => <option key={a} value={a}>{a}</option>)}
              </select>
            </div>

            <div className="opt-input-group">
              <label className="opt-label">Amount (USD)</label>
              <input
                className="opt-input"
                type="number"
                value={amount}
                onChange={(e) => setAmount(Number(e.target.value) || 0)}
                min={0}
                step={100000}
              />
            </div>

            <div className="opt-input-group">
              <label className="opt-label">Holding Period</label>
              <div className="opt-toggle-group">
                {HOLDING_PERIODS.map((hp) => (
                  <button
                    key={hp.days}
                    className={`opt-toggle ${period === hp.days ? "opt-toggle-active" : ""}`}
                    onClick={() => setPeriod(hp.days)}
                  >
                    {hp.label}
                  </button>
                ))}
              </div>
            </div>

            <div className="opt-input-group">
              <label className="opt-label">Risk Tolerance</label>
              <div className="opt-toggle-group">
                {RISK_LEVELS.map((rl) => (
                  <button
                    key={rl}
                    className={`opt-toggle ${risk === rl ? "opt-toggle-active" : ""}`}
                    onClick={() => setRisk(rl)}
                  >
                    {rl.charAt(0).toUpperCase() + rl.slice(1)}
                  </button>
                ))}
              </div>
            </div>

            {/* Advanced settings */}
            <button
              className="opt-advanced-toggle"
              onClick={() => setShowAdvanced(!showAdvanced)}
            >
              {showAdvanced ? "\u25B2" : "\u25BC"} Advanced Settings
            </button>

            {showAdvanced && (
              <div className="opt-advanced">
                <div className="opt-input-group">
                  <label className="opt-label">Preferred Chains</label>
                  <div className="opt-chain-chips">
                    {CHAINS.map((c) => (
                      <button
                        key={c}
                        className={`opt-chip ${chains.includes(c) ? "opt-chip-active" : ""}`}
                        onClick={() => toggleChain(c)}
                      >
                        {c}
                      </button>
                    ))}
                  </div>
                  {chains.length === 0 && (
                    <span className="opt-hint">All chains (none selected)</span>
                  )}
                </div>

                <div className="opt-input-group">
                  <label className="opt-label">Max Conversion Steps</label>
                  <input
                    className="opt-input opt-input-sm"
                    type="number"
                    value={maxSteps}
                    onChange={(e) => setMaxSteps(Math.max(1, Math.min(5, Number(e.target.value) || 3)))}
                    min={1}
                    max={5}
                  />
                </div>

                <label className="opt-checkbox">
                  <input
                    type="checkbox"
                    checked={includeBorrow}
                    onChange={(e) => setIncludeBorrow(e.target.checked)}
                  />
                  Include borrow routes
                </label>

                <label className="opt-checkbox">
                  <input
                    type="checkbox"
                    checked={excludePendle}
                    onChange={(e) => setExcludePendle(e.target.checked)}
                  />
                  Exclude Pendle PT/YT
                </label>
              </div>
            )}

            <button
              className="opt-btn opt-btn-primary opt-btn-find"
              onClick={findRoutes}
              disabled={loading || amount <= 0}
            >
              {loading ? "Computing..." : "Find Routes"}
            </button>
          </div>

          {/* Results panel */}
          <div className="opt-results">
            <ResultsPanel result={result} loading={loading} />
          </div>
        </div>
      ) : (
        <ComparePanel />
      )}
    </div>
  );
}
