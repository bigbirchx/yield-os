# Source Map

## Derivatives
- funding / OI / basis / spot-perp volume -> Velo
- direct venue checks / fallback -> public exchange endpoints where needed

## DeFi lending / yields / history
- current borrow and lend rates -> DeFiLlama
- historical lend / borrow charts -> DeFiLlama

## Staking / LST
- LSD and broad staking rates -> DeFiLlama initially
- protocol-native / manual metadata for exact exit rules

## Collateral parameters / risk params
- Aave -> direct protocol docs / utilities / views
- Morpho -> public GraphQL API
- Kamino -> public REST API

## Wrappers / mint-redeem / stablecoin economics
- manual metadata in MVP
- upgrade later with issuer-specific connectors where necessary

## Events
- DeFiLlama unlocks / ecosystem references initially
- manual overlays for governance, listings, major news, and internal notes

## Internal logic
- quote buffers, concentration limits, pool-share limits, fungibility classes, and route preferences -> manual internal metadata tables
