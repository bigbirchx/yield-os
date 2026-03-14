# Yield Cockpit MVP

## Goal
Build an institutional crypto yield cockpit that helps a lending/borrowing desk monitor current and historical rates, derivatives dislocations, executable liquidity, collateral parameters, and route economics.

## Primary User
Internal crypto credit / financing desk.

## Core Questions the Product Must Answer
1. What can we earn on asset X right now?
2. What does it cost to source or borrow asset X right now?
3. Why is borrow demand elevated or depressed for asset X?
4. How much size is actually executable without becoming the market?
5. What should we quote a client, net of liquidity, transform costs, and risk buffers?

## MVP Scope
### Asset families
- BTC family
- ETH family
- SOL family
- USD stablecoin family

### Protocols / venues
- Velo for derivatives aggregation
- DeFiLlama for broad DeFi/yield/history coverage
- Aave
- Morpho
- Kamino

### MVP screens
- Market overview
- Asset cockpit
- Capacity / LTV matrix
- Borrow-demand explainer
- Route / quote prototype

## Data-source philosophy
- Use direct protocol data where quote-quality precision matters
- Use Velo as the default derivatives source
- Use DeFiLlama as the default broad DeFi/yield/history source
- Preserve raw payloads for reconciliation
- Show source labels and freshness timestamps in the UI

## Out of Scope for MVP
- Auth / permissions
- OMS / EMS integration
- Actual trading or execution
- Full internal inventory integration
- Client-facing portal
- Multi-tenant support

## Product principles
- Accuracy over breadth
- Explainability over black-box scoring
- Institutional UI, not retail crypto UI
- Every important metric should be traceable to a source
- Keep the implementation simple and maintainable
