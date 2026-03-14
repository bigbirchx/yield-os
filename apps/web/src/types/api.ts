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
