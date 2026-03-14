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
