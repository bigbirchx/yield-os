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
| Asset price, 24h volume, market cap | CoinGecko Pro | `COINGECKO_API_KEY` |
| Global market stats (BTC dom, total cap) | CoinGecko Pro | `COINGECKO_API_KEY` |

## Derivatives Snapshot (DB)

Written to `derivatives_snapshots` table by:
1. **Internal connectors** (`internal_ingestion.py`) — Binance + OKX via REST, every 5 min
2. **Velo** (`velo_ingestion.py`) — broader cross-venue history, requires `VELO_API_KEY`

The overview page (`/overview`) reads from this table.

## Internal Reference Codebase (optional)

Mounted read-only at `/home/ec2-user/workspace` inside the API Docker container.
When present, unlocks:
- 3-year MongoDB-backed funding rate history (Binance, OKX)
- Extended PerpFuture history fetchers (Bybit, Deribit)

All critical paths have REST fallbacks and work without this mount.
