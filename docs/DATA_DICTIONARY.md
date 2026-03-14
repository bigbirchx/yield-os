# Data Dictionary

## funding_rate
Periodic perpetual swap funding rate for an instrument on a venue.

## open_interest_usd
Open interest for a derivative instrument, normalized to USD notional.

## basis_annualized
Annualized difference between futures price and spot/index reference price.

## supply_apy
Annualized yield earned by supplying an asset to a lending market.

## borrow_apy
Annualized cost paid to borrow an asset from a lending market.

## utilization
Borrowed amount divided by supplied liquidity in a market.

## tvl_usd
Total value locked, normalized to USD.

## available_liquidity_usd
Estimated immediately borrowable or withdrawable liquidity, normalized to USD.

## borrow_cap_usd
Protocol borrow cap for an asset/market normalized to USD where possible.

## supply_cap_usd
Protocol supply cap for an asset/market normalized to USD where possible.

## max_ltv
Maximum loan-to-value allowed when using an asset as collateral.

## liquidation_threshold
Collateral threshold beyond which liquidation eligibility begins.

## executable_capacity_usd
Conservative estimate of how much size can be deployed or borrowed without breaching internal share-of-pool limits or hard capacity constraints.

## transform_fee_bps
Fee in basis points for converting one asset form into another, such as mint, redeem, wrap, unwrap, stake, unstake, or bridge.

## transform_latency_seconds
Estimated delay in seconds for an asset transformation path.

## unbonding_days
Expected lock/unbonding time for native staking exits.

## wrapper_fungibility_class
Internal classification describing whether two assets are treated as fungible, quasi-fungible, or non-fungible for routing purposes.

## source_confidence
Internal confidence score for a datapoint based on source quality, freshness, and normalization quality.

## reason_score
Score representing how strongly a given factor explains current borrow demand or rate richness.

## recommended_quote
Suggested client quote after applying source cost, buffers, and target spread.
