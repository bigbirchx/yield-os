# Source Map

## Perpetual Funding Rates

| Field | Primary | Fallback | Key? |
|-------|---------|----------|------|
| Live rate (Binance) | Internal connector (`get_binance_predicted_funding_rate`) | `fapi.binance.com/fapi/v1/premiumIndex` | No |
| Live rate (OKX) | Internal connector (`get_okx_funding_rate`) | `okx.com/api/v5/public/funding-rate` | No |
| Live rate (Bybit) | `api.bybit.com/v5/market/tickers` | — | No |
| Live rate (Deribit) | `deribit.com/api/v2/public/ticker?instrument_name=BTC-PERPETUAL` | — | No |
| History (Binance, OKX) | MongoDB via internal connector | `fapi.binance.com/fapi/v1/fundingRate` / OKX REST | No |
| History (Bybit) | `PerpFuture._get_bybit_funding_rate_history` | `api.bybit.com/v5/market/funding/history` | No |
| History (Deribit) | `PerpFuture._get_deribit_funding_rate_history` | `deribit.com/api/v2/public/get_funding_rate_history` | No |
| OI (Binance) | Internal connector | `fapi.binance.com/fapi/v1/openInterest` | No |
| OI (OKX) | `okx.com/api/v5/public/open-interest` | — | No |
| OI (Bybit) | `api.bybit.com/v5/market/tickers` | — | No |
| Blended APR | Computed from live rates (equal / OI-weighted / volume-weighted) | — | — |
| Cross-check | Coinglass | — | `COINGLASS_API_KEY` (optional) |
| Bullish | Bullish institutional exchange | — | `BULLISH_PUBLIC_KEY` / `BULLISH_PRIVATE_KEY` |

## Dated Futures Basis

| Field | Primary | Key? |
|-------|---------|------|
| Deribit snapshot | `get_book_summary_by_currency` + `get_index_price` | No |
| Deribit history | `get_tradingview_chart_data` (futures + index OHLC) | No |
| Binance snapshot | FAPI `exchangeInfo` + `premiumIndex` + `openInterest` | No |
| Binance history | FAPI `klines` (daily close) | No |
| OKX snapshot | `public/instruments` + `mark-price` + `open-interest` + `ticker` | No |
| OKX history | `market/candles` (daily) | No |
| Bybit snapshot | `market/instruments-info` + `market/tickers` | No |
| Bybit history | `market/kline` (daily) | No |
| CME snapshot | Amberdata `markets/futures/ohlcv/{code}` | `AMBERDATA_DERIVS_KEY` |
| CME history | Amberdata OHLCV | `AMBERDATA_DERIVS_KEY` |

## DeFi Lending / Yields

| Data | Source | Key? |
|------|--------|------|
| Current borrow + lend APY | DeFiLlama `/pools` | No |
| Historical APY charts | DeFiLlama `/chart/{pool_id}` | No |
| Utilization, supply/borrow caps | Aave official GraphQL (`api.v3.aave.com/graphql`) | No |
| Morpho vault/market data | Morpho GraphQL (`blue-api.morpho.org`) | No |
| Kamino Solana markets | Kamino REST (`api.kamino.finance`) | No |

## Staking / LST

| Data | Source | Key? |
|------|--------|------|
| LST and broad staking rates | DeFiLlama | No |

## Collateral / Risk Parameters

| Data | Source | Key? |
|------|--------|------|
| Aave LTV, liquidation thresholds, caps | Aave GraphQL | No |
| Morpho borrow limits, collateral | Morpho GraphQL | No |
| Kamino lending limits | Kamino REST | No |

## Market Reference Data

| Data | Source | Key? |
|------|--------|------|
| Asset price, 24h volume, market cap | CoinGecko (Demo/Pro/Free auto-detected) | `COINGECKO_API_KEY` (optional) |
| Global market stats (BTC dom, total cap) | CoinGecko (Demo/Pro/Free auto-detected) | `COINGECKO_API_KEY` (optional) |

**CoinGecko key tier detection** — `coingecko_client.py` inspects the key prefix at startup:
- No key → `api.coingecko.com`, no auth header (free public tier)
- `CG-` prefix → `api.coingecko.com` + `x-cg-demo-api-key` (Demo tier)
- Other → `pro-api.coingecko.com` + `x-cg-pro-api-key` (Pro tier)

## Derivatives Snapshot (DB)

Written to `derivatives_snapshots` table by:
1. **Internal connectors** (`internal_ingestion.py`) — Binance + OKX via REST, every 5 min
2. **Velo** (`velo_ingestion.py`) — broader cross-venue history, requires `VELO_API_KEY`

The overview page (`/overview`) reads from this table.

## Asset Alias Groups

Assets with multiple on-chain representations are tracked together under a canonical symbol:

| Canonical | Aliases tracked |
|-----------|----------------|
| BTC | BTC, WBTC, CBBTC, BTCB |
| ETH | ETH, WETH |
| SOL | SOL |
| USDC | USDC |
| USDT | USDT |
| DAI | DAI |

`SYMBOL_ALIASES` in `defillama_ingestion.py` governs DeFiLlama lookup expansion.
`CBBTC` (`coinbase-wrapped-btc` on CoinGecko) is tracked individually in
`coingecko_ingestion.py` alongside WBTC and included in `borrow_demand_loader`
and `route_optimizer_loader` BTC equivalence groups.

## Frontend UI Components

| Component | Location | Purpose |
|-----------|----------|---------|
| `AssetLookup` | `components/overview/AssetLookup.tsx` | Search input on Overview page; opens `/assets/{SYMBOL}` in a new tab |
| `LendingRateSections` | `components/overview/LendingRateSections.tsx` | Client-side interactive filters (Min TVL, Asset, Min Availability) for lending rate sections |
| `FundingRatesDrawer` | `components/asset/FundingRatesDrawer.tsx` | Collapsible bar on asset pages; lazily loads full funding dashboard on first expand |
| `FundingDashboard` | `components/funding/FundingDashboard.tsx` | Extracted reusable funding dashboard; accepts `initialSymbol` and `showSymbolPicker` props |

## Book / Portfolio Overlay

| Data | Source | Key? |
|------|--------|------|
| Positions, counterparties, collateral | CreditDesk WACC Export (Excel upload) | No |
| Position classification | Auto-classified by `book_import.py` (regex on counterparty names) | No |
| Rate comparison benchmarks | `market_opportunities` table (DeFi/CeFi pools with $5M+ TVL) | No |
| Optimization suggestions | `book_optimizer.py` (8 analysis passes against market data) | No |
| Route conversion analysis | `route-optimizer` package (multi-hop yield routes) | No |

## Internal Reference Codebase (optional)

Mounted read-only at `/home/ec2-user/workspace` inside the API Docker container.
When present, unlocks:
- 3-year MongoDB-backed funding rate history (Binance, OKX)
- Extended PerpFuture history fetchers (Bybit, Deribit)

All critical paths have REST fallbacks and work without this mount.
