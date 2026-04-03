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

// -----------------------------------------------------------------------
// Unified Market Opportunities (source: /api/opportunities)
// -----------------------------------------------------------------------

export interface MarketOpportunity {
  opportunity_id: string;
  venue: string;
  chain: string;
  protocol: string;
  protocol_slug: string;
  market_id: string;
  market_name: string | null;
  side: "SUPPLY" | "BORROW";
  asset_id: string;
  asset_symbol: string;
  umbrella_group: string;
  asset_sub_type: string;
  opportunity_type: string;
  effective_duration: string;
  maturity_date: string | null;
  days_to_maturity: number | null;
  total_apy_pct: number;
  base_apy_pct: number;
  reward_breakdown: { apy_pct: number; notes?: string | null; reward_type?: string; token_id?: string | null; token_name?: string | null; is_variable?: boolean }[];
  total_supplied: number | null;
  total_supplied_usd: number | null;
  total_borrowed: number | null;
  total_borrowed_usd: number | null;
  capacity_cap: number | null;
  capacity_remaining: number | null;
  is_capacity_capped: boolean;
  tvl_usd: number | null;
  liquidity: Record<string, unknown>;
  rate_model: Record<string, unknown> | null;
  is_collateral_eligible: boolean;
  as_collateral_max_ltv_pct: number | null;
  as_collateral_liquidation_ltv_pct: number | null;
  collateral_options: Record<string, unknown>[] | null;
  receipt_token: Record<string, unknown> | null;
  is_amm_lp: boolean;
  is_pendle: boolean;
  pendle_type: string | null;
  tags: string[];
  data_source: string;
  last_updated_at: string;
  data_freshness_seconds: number;
  source_url: string | null;
}

export interface OpportunityRatePoint {
  snapshot_at: string;
  total_apy_pct: number;
  base_apy_pct: number;
  total_supplied: number | null;
  total_supplied_usd: number | null;
  total_borrowed: number | null;
  total_borrowed_usd: number | null;
  utilization_rate_pct: number | null;
  tvl_usd: number | null;
}

export interface OpportunitySummary {
  total_opportunities: number;
  by_venue: Record<string, number>;
  by_chain: Record<string, number>;
  by_type: Record<string, number>;
  by_umbrella: Record<string, number>;
  by_side: Record<string, number>;
  top_supply_apy: { id: string; asset: string; venue: string; chain: string; apy: number }[] | null;
  top_borrow_apy: { id: string; asset: string; venue: string; chain: string; apy: number }[] | null;
}

export interface PaginationMeta {
  total: number;
  limit: number;
  offset: number;
  has_more: boolean;
}

export interface PaginatedResponse<T> {
  data: T[];
  pagination: PaginationMeta;
}

export interface RefreshResult {
  triggered_at: string;
  total_opportunities: number;
  by_venue: Record<string, number>;
  errors: string[];
  duration_seconds: number;
}

// -----------------------------------------------------------------------
// Token Universe (source: /api/tokens)
// -----------------------------------------------------------------------

export interface Token {
  canonical_id: string;
  coingecko_id: string | null;
  name: string;
  symbol: string;
  umbrella: string;
  sub_type: string;
  market_cap_rank: number | null;
  market_cap_usd: number | null;
  current_price_usd: number | null;
  price_updated_at: string | null;
  chains: string[];
  is_static: boolean;
  last_refreshed_at: string;
}

export interface TokenDetail extends Token {
  opportunity_count: number;
  opportunity_count_by_protocol: Record<string, number>;
  top_supply_apy: number | null;
  top_borrow_apy: number | null;
}

// -----------------------------------------------------------------------
// Worker Health (source: /api/admin/worker-health)
// -----------------------------------------------------------------------

export interface WorkerHealth {
  worker_status: "healthy" | "degraded" | "down";
  last_heartbeat: string | null;
  heartbeat_age_seconds: number | null;
  jobs: Record<string, {
    last_run: string;
    last_success: string | null;
    last_status: "success" | "error";
    consecutive_failures: number;
    duration_seconds: number;
    detail?: Record<string, unknown>;
  }>;
  total_opportunities: number;
  by_venue: Record<string, number>;
  by_chain: Record<string, number>;
}

// -----------------------------------------------------------------------
// Opportunity Filter Params (used by FilterBar + API client)
// -----------------------------------------------------------------------

export interface OpportunityFilters {
  umbrella?: string;
  side?: string;
  type?: string;
  chain?: string;
  venue?: string;
  asset?: string;
  min_apy?: number;
  min_tvl?: number;
  exclude_amm_lp?: boolean;
  exclude_pendle?: boolean;
  sort_by?: string;
  limit?: number;
  offset?: number;
}

export interface TokenFilters {
  search?: string;
  umbrella?: string;
  min_rank?: number;
  max_rank?: number;
  limit?: number;
  offset?: number;
}

// -----------------------------------------------------------------------
// Yield Route Optimizer (source: /api/optimizer)
// -----------------------------------------------------------------------

export interface ConversionStep {
  from_asset: string;
  to_asset: string;
  method: string;
  chain: string;
  protocol: string | null;
  fee_bps: number;
  slippage_bps: number;
  gas_usd: number;
}

export interface CollateralInfo {
  collateral_asset: string;
  collateral_amount_usd: number;
  max_ltv_pct: number;
  liquidation_ltv_pct: number;
  liquidation_buffer_pct: number;
  conversion_cost_bps: number;
  opportunity_cost_apy_pct: number;
}

export interface YieldRouteResult {
  opportunity_id: string;
  venue: string;
  chain: string;
  protocol: string;
  market_name: string | null;
  side: "SUPPLY" | "BORROW";
  target_asset: string;
  umbrella_group: string;
  opportunity_type: string;
  conversion_steps: ConversionStep[];
  conversion_cost_bps: number;
  conversion_gas_usd: number;
  conversion_time_seconds: [number, number];
  is_conversion_deterministic: boolean;
  gross_apy_pct: number;
  net_apy_pct: number;
  annualized_conversion_cost_pct: number;
  max_deployable_usd: number;
  capacity_limited: boolean;
  tvl_usd: number | null;
  rate_impact_bps: number;
  post_deposit_apy_pct: number | null;
  risk_flags: string[];
  risk_score: number;
  collateral: CollateralInfo | null;
  computed_at: string;
}

export interface OptimizerResponse {
  entry_asset: string;
  entry_amount_usd: number;
  holding_period_days: number;
  total_routes: number;
  routes: YieldRouteResult[];
  best_supply_route: YieldRouteResult | null;
  best_borrow_route: YieldRouteResult | null;
  computed_at: string;
}

export interface OptimizerCompareResponse {
  comparisons: OptimizerResponse[];
  computed_at: string;
}

// -----------------------------------------------------------------------
// Book / Portfolio (source: /api/book)
// -----------------------------------------------------------------------

export interface BookSummary {
  total_positions: number;
  total_loan_out_usd: number;
  total_borrow_in_usd: number;
  net_book_usd: number;
  defi_deployed_usd: number;
  defi_borrowed_usd: number;
  staking_deployed_usd: number;
  bilateral_loan_out_usd: number;
  bilateral_borrow_in_usd: number;
  weighted_avg_lending_rate_pct: number;
  weighted_avg_borrowing_rate_pct: number;
  net_interest_margin_pct: number;
  estimated_daily_income_usd: number;
  estimated_annual_income_usd: number;
  positions_by_asset: Record<string, number>;
  positions_by_counterparty: Record<string, number>;
  positions_by_category: Record<string, number>;
  defi_positions_vs_market: BookDefiComparison[];
}

export interface BookDefiComparison {
  loan_id: number;
  protocol_name: string | null;
  protocol_chain: string | null;
  principal_asset: string;
  direction: string;
  our_rate_pct: number;
  market_rate_pct: number;
  rate_diff_bps: number | null;
  principal_usd: number;
}

export interface BookImportResult {
  book_id: string;
  total_positions: number;
  total_collateral_observations: number;
  total_allocations: number;
  category_breakdown: Record<string, number>;
  total_loan_out_usd: number;
  total_borrow_in_usd: number;
  net_book_usd: number;
  weighted_avg_lending_rate_pct: number;
  weighted_avg_borrowing_rate_pct: number;
  net_interest_margin_pct: number;
}

export interface BookMeta {
  book_id: string;
  name: string;
  source_file: string;
  import_date: string;
  as_of_date: string | null;
  total_positions: number;
  summary: BookSummary | null;
}

export interface BookPosition {
  loan_id: number;
  customer_id: number;
  counterparty_name: string;
  counterparty_legal_entity: string | null;
  category: string;
  direction: string;
  principal_asset: string;
  principal_qty: number;
  principal_usd: number;
  effective_date: string | null;
  maturity_date: string | null;
  tenor: string;
  recall_period_days: number | null;
  collateral_assets_raw: string | null;
  initial_collateralization_ratio_pct: number | null;
  rehypothecation_allowed: boolean;
  collateral_substitution_allowed: boolean;
  is_collateralized: boolean;
  loan_type: string;
  interest_rate_pct: number;
  status: string;
  query_notes: string | null;
  protocol_name: string | null;
  protocol_chain: string | null;
  umbrella_group: string | null;
  matched_opportunity_id: string | null;
  current_market_rate_pct: number | null;
  rate_vs_market_bps: number | null;
}

export interface BookCollateralData {
  observed: {
    customer_id: number;
    counterparty_name: string;
    collateral_relationship: string;
    collateral_asset: string;
    units_posted: number;
    data_source: string;
    is_tri_party: boolean;
    custodial_venue: string;
  }[];
  allocations: {
    loan_id: number;
    collateral_asset: string;
    allocated_units: number;
    allocated_usd: number;
    allocation_weight_pct: number;
  }[];
}

export interface BookOptimizationSuggestion {
  suggestion_id: string;
  type: string;
  priority: "high" | "medium" | "low";
  position: Record<string, unknown>;
  current_rate_pct: number;
  market_rate_pct: number;
  suggested_opportunity: Record<string, unknown> | null;
  suggested_route: Record<string, unknown> | null;
  rate_improvement_bps: number;
  estimated_annual_impact_usd: number;
  switching_cost_usd: number;
  break_even_days: number;
  risk_assessment: string;
  action_description: string;
  execution_steps: string[];
}

export interface BookAnalysisResult {
  book_id: string;
  analyzed_at: string;
  total_positions_analyzed: number;
  total_opportunities_scanned: number;
  total_suggestions: number;
  total_estimated_annual_impact_usd: number;
  suggestions_by_type: Record<string, number>;
  suggestions_by_priority: Record<string, number>;
  suggestions: BookOptimizationSuggestion[];
}

export interface DefiVsMarketRow {
  loan_id: number;
  protocol_name: string | null;
  protocol_chain: string | null;
  asset: string;
  direction: string;
  category: string;
  principal_usd: number;
  our_rate_pct: number;
  matched_market_rate_pct: number | null;
  delta_vs_matched_bps: number | null;
  best_market_rate_pct: number | null;
  best_market_protocol: string | null;
  delta_vs_best_bps: number | null;
}

export interface BilateralPricingRow {
  loan_id: number;
  counterparty_name: string;
  customer_id: number;
  direction: string;
  asset: string;
  principal_usd: number;
  our_rate_pct: number;
  best_defi_rate_pct: number | null;
  best_defi_protocol: string | null;
  defi_rate_range_min: number | null;
  defi_rate_range_max: number | null;
  premium_discount_bps: number | null;
  assessment: string;
  is_collateralized: boolean;
  tenor: string;
}

export interface CollateralEfficiencyRow {
  customer_id: number;
  counterparty_name: string;
  total_loans_usd: number;
  total_collateral_usd: number;
  total_required_usd: number;
  excess_usd: number;
  excess_pct: number;
  rehypothecation_allowed: boolean;
  collateral_assets: string[];
  potential_yield_usd: number;
  potential_yield_details: { asset: string; best_rate_pct: number; protocol: string | null; estimated_yield_usd: number }[];
  status: string;
}

export interface MaturityCalendarRow {
  loan_id: number;
  counterparty_name: string;
  customer_id: number;
  direction: string;
  category: string;
  asset: string;
  principal_usd: number;
  interest_rate_pct: number;
  maturity_date: string;
  days_to_maturity: number;
  status: string;
  current_market_rate_pct: number | null;
  market_protocol: string | null;
  rate_delta_bps: number | null;
}

export interface OptimizerRequestConfig {
  exclude_amm_lp?: boolean;
  exclude_pendle?: boolean;
  include_borrow_routes?: boolean;
  max_ltv_pct?: number;
  risk_tolerance?: "conservative" | "moderate" | "aggressive";
  preferred_chains?: string[];
  max_conversion_steps?: number;
  min_tvl_usd?: number;
}
