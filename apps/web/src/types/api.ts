// -----------------------------------------------------------------------
// Derivatives (source: Velo)
// -----------------------------------------------------------------------

export interface DerivativesSnapshot {
  symbol: string;
  venue: string;
  funding_rate: number | null;
  open_interest_usd: number | null;
  basis_annualized: number | null;
  mark_price: number | null;
  index_price: number | null;
  spot_volume_usd: number | null;
  perp_volume_usd: number | null;
  snapshot_at: string;
  ingested_at: string;
}

export interface DerivativesOverview {
  symbol: string;
  venues: DerivativesSnapshot[];
}

// -----------------------------------------------------------------------
// Lending (source: DeFiLlama)
// -----------------------------------------------------------------------

export interface LendingMarket {
  symbol: string;
  protocol: string;
  market: string;
  chain: string | null;
  pool_id: string | null;
  supply_apy: number | null;
  borrow_apy: number | null;
  reward_supply_apy: number | null;
  reward_borrow_apy: number | null;
  utilization: number | null;
  tvl_usd: number | null;
  available_liquidity_usd: number | null;
  snapshot_at: string;
  ingested_at: string;
}

export interface LendingOverview {
  symbol: string;
  markets: LendingMarket[];
}

// -----------------------------------------------------------------------
// Staking (source: DeFiLlama)
// -----------------------------------------------------------------------

export interface StakingSnapshot {
  symbol: string;
  underlying_symbol: string;
  protocol: string;
  chain: string;
  pool_id: string | null;
  staking_apy: number | null;
  base_apy: number | null;
  reward_apy: number | null;
  tvl_usd: number | null;
  snapshot_at: string;
  ingested_at: string;
}

// -----------------------------------------------------------------------
// Protocol risk params (source: Aave, Morpho, Kamino)
// -----------------------------------------------------------------------

export interface ProtocolRiskParams {
  protocol: string;
  chain: string;
  asset: string;
  debt_asset: string | null;
  market_address: string | null;
  max_ltv: number | null;
  liquidation_threshold: number | null;
  liquidation_penalty: number | null;
  borrow_cap_native: number | null;
  supply_cap_native: number | null;
  collateral_eligible: boolean | null;
  borrowing_enabled: boolean | null;
  is_active: boolean | null;
  available_capacity_native: number | null;
  snapshot_at: string;
  ingested_at: string;
}

// -----------------------------------------------------------------------
// Asset history (source: DeFiLlama historical charts)
// -----------------------------------------------------------------------

export interface LendingHistoryPoint {
  snapshot_at: string;
  supply_apy: number | null;
  borrow_apy: number | null;
  reward_supply_apy: number | null;
  tvl_usd: number | null;
  utilization: number | null;
}

export interface LendingHistoryMarket {
  protocol: string;
  market: string;
  chain: string | null;
  data: LendingHistoryPoint[];
}

export interface AssetHistory {
  symbol: string;
  lending: LendingHistoryMarket[];
}

// -----------------------------------------------------------------------
// Borrow-demand explanation engine (source: internal engine)
// -----------------------------------------------------------------------

export interface ReasonFactor {
  name: string;
  display_label: string;
  direction: "elevates" | "suppresses" | "neutral";
  score: number;
  value: number | null;
  baseline: number | null;
  value_unit: string;
  metric_source: string;
  metric_name: string;
  snapshot_at: string | null;
  evidence_note: string;
}

export interface EventOverlay {
  label: string;
  event_date: string;
  impact: "elevates" | "suppresses" | "neutral";
  source: string;
  notes: string;
}

export interface BorrowDemandAnalysis {
  symbol: string;
  demand_level: "elevated" | "normal" | "suppressed";
  demand_score: number;
  confidence: number;
  reasons: ReasonFactor[];
  explanation: string;
  computed_at: string;
  data_window_days: number;
  event_overlays: EventOverlay[];
}

// -----------------------------------------------------------------------
// Route optimizer
// -----------------------------------------------------------------------

export interface CostComponent {
  name: string;
  value_bps: number;
  source: string;
  is_assumption: boolean;
}

export interface RouteBottleneck {
  constraint: string;
  limiting_factor: string;
  severity: "hard" | "soft";
  value: number | null;
  value_unit: string;
}

export interface OptimizedRoute {
  route_type: string;
  display_name: string;
  description: string;
  rank: number;
  total_cost_bps: number;
  effective_cost_bps: number;
  max_executable_usd: number;
  feasible: boolean;
  cost_components: CostComponent[];
  bottlenecks: RouteBottleneck[];
  assumptions_used: string[];
  ranking_rationale: string;
}

export interface RouteOptimizerAssumptions {
  max_pool_share: number;
  max_oi_share: number;
  spot_slippage_bps: number;
  funding_variance_premium_bps: number;
  wrapper_extra_slippage_bps: number;
  unbonding_bps_per_day: number;
  size_shortfall_penalty_bps: number;
}

export interface RouteOptimizerResult {
  target_asset: string;
  request_size_usd: number;
  recommended_route: string;
  summary: string;
  routes: OptimizedRoute[];
  computed_at: string;
  assumptions: RouteOptimizerAssumptions;
}

// -----------------------------------------------------------------------
// Data source status (source: /api/admin/sources)
// -----------------------------------------------------------------------

export interface SourceStatus {
  key: string;
  label: string;
  status: "fresh" | "stale" | "missing";
  last_updated: string | null;
  row_count: number;
  stale_threshold_minutes: number;
  populates: string[];
}

// -----------------------------------------------------------------------
// CoinGecko reference layer (source: coingecko)
// -----------------------------------------------------------------------

export interface MarketSnapshot {
  symbol: string;
  coingecko_id: string;
  current_price_usd: number | null;
  market_cap_usd: number | null;
  fully_diluted_valuation_usd: number | null;
  volume_24h_usd: number | null;
  circulating_supply: number | null;
  total_supply: number | null;
  max_supply: number | null;
  price_change_24h_pct: number | null;
  snapshot_at: string;
  source_name: string;
}

export interface AssetDetail {
  symbol: string;
  canonical_symbol: string;
  coingecko_id: string | null;
  name: string | null;
  asset_type: string | null;
  chain: string | null;
  contract_address: string | null;
  market: MarketSnapshot | null;
  source_name: string;
}

export interface MarketHistoryPoint {
  snapshot_at: string;
  price_usd: number | null;
  market_cap_usd: number | null;
  volume_24h_usd: number | null;
}

export interface AssetMarketHistory {
  symbol: string;
  coingecko_id: string;
  series: MarketHistoryPoint[];
}

export interface GlobalMarket {
  total_market_cap_usd: number | null;
  total_volume_24h_usd: number | null;
  btc_dominance_pct: number | null;
  eth_dominance_pct: number | null;
  market_cap_change_24h_pct: number | null;
  active_cryptocurrencies: number | null;
  source_name: string;
  fetched_at: string;
}

export interface ApiUsage {
  provider: string;
  rate_limit: number | null;
  remaining_credits: number | null;
  monthly_total_credits: number | null;
  snapshot_at: string | null;
  source_name: string;
}

// -----------------------------------------------------------------------
// Flattened rows used by overview cards
// -----------------------------------------------------------------------

export interface FlatLendingRow extends LendingMarket {
  /** Top-level symbol from the outer LendingOverview wrapper */
  asset: string;
}

export interface FlatDerivativesRow extends DerivativesSnapshot {
  /** Redundant but explicit for clarity in card rendering */
  asset: string;
}
