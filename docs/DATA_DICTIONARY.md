# Data Dictionary

## Funding / Perpetuals

### funding_rate
Periodic perpetual swap funding rate for an instrument on a venue.
Raw value is the per-8-hour rate (e.g. 0.0001 = 0.01% per 8h).
Stored as the 8h rate in `derivatives_snapshots.funding_rate`.

### annualized_funding_rate
`funding_rate × 3 × 365`. Represents the cost of holding a perpetual
position for one year at the current rate. Used in all charts and tables.
0.10 = 10% p.a.

### funding_interval_hours
Observed interval between consecutive funding rate payments (usually 8h).
Inferred from history timestamps.

### open_interest_usd
Open interest for a derivative instrument, normalized to USD notional.

### volume_24h_usd
24-hour trading volume normalized to USD.

## Basis / Dated Futures

### basis_usd
`futures_price − index_price`.
Positive = futures trading at a premium to spot (contango).
Negative = futures trading at a discount (backwardation).

### basis_pct_term
`basis_usd / index_price`. Raw basis as a fraction of spot for the
full remaining term.

### basis_pct_ann
`basis_pct_term × (365 / days_to_expiry)`.
Annualized basis — the theoretical yield from a cash-and-carry trade
(long spot + short futures) if held to expiry.
Stored as a decimal (0.05 = 5% p.a.).

### days_to_expiry (dte)
Calendar days from now to contract expiry. Contracts with DTE ≤ 0 are excluded.

### futures_price
Mark price of the dated futures contract (USD-denominated).

### index_price
Current spot index price for the underlying asset. For Deribit this is the
Deribit index (e.g. `btc_usd`). For Binance/OKX/Bybit this is the composite
index from the exchange. For CME this is the Binance spot price as a BRR proxy.

## Lending / DeFi

### supply_apy
Annualized yield earned by supplying an asset to a lending market.
Includes base rate and any reward APY where applicable.

### borrow_apy
Annualized cost paid to borrow an asset from a lending market.

### reward_supply_apy / reward_borrow_apy
Token incentive component of the supply/borrow APY.

### utilization
Borrowed amount divided by total supplied liquidity in a market.
At 1.0 (100%) the pool is fully utilized; no more borrows possible.

### tvl_usd
Total value locked, normalized to USD.

### available_liquidity_usd
Estimated immediately borrowable or withdrawable liquidity, normalized to USD.
`= total_supplied − total_borrowed` (approximate).

### borrow_cap_usd
Protocol hard cap on borrowing for a given asset/market, normalized to USD.

### supply_cap_usd
Protocol hard cap on supply for a given asset/market, normalized to USD.

## Risk Parameters (Collateral)

### max_ltv
Maximum loan-to-value ratio allowed when using an asset as collateral.
0.75 = can borrow up to 75% of the collateral's value.

### liquidation_threshold
The LTV at which a position becomes eligible for liquidation.
Always ≥ max_ltv.

### liquidation_bonus
Extra discount rewarded to liquidators. 0.05 = 5% bonus above fair value.

## Internal / Derived

### executable_capacity_usd
Conservative estimate of how much size can be deployed or borrowed without
breaching internal share-of-pool limits or hard capacity constraints.

### transform_fee_bps
Fee in basis points for converting one asset form into another (mint, redeem,
wrap, unwrap, stake, unstake, bridge).

### transform_latency_seconds
Estimated delay for an asset transformation path to complete.

### unbonding_days
Expected lock/unbonding time for native staking exits.

### wrapper_fungibility_class
Internal classification: `fungible`, `quasi_fungible`, or `non_fungible`.
Determines whether two assets are treated as interchangeable for routing.

### source_confidence
Internal confidence score (0–1) for a data point based on source quality,
freshness, and normalization quality.

### reason_score
Score (0–1) representing how strongly a given factor explains current borrow
demand or rate richness.

### recommended_quote
Suggested client quote after applying source cost, buffers, and target spread.
