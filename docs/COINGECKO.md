# CoinGecko Integration

CoinGecko serves as the **canonical market reference and asset metadata layer** for Yield OS. It is NOT the source of truth for protocol-native lending parameters, LTVs, or derivatives routing — those come from DeFiLlama, Aave, Morpho, Kamino, and Velo respectively.

---

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `COINGECKO_API_KEY` | `""` | Pro API key. If unset, the free public endpoint is used automatically. |
| `COINGECKO_BASE_URL` | `https://pro-api.coingecko.com/api/v3` | Override the base URL (e.g. for testing). |

The API key is passed via the `x-cg-pro-api-key` header and is **never exposed to the browser**.

---

## Tracked assets (MVP)

| Symbol | CoinGecko ID | Type |
|---|---|---|
| BTC | bitcoin | crypto |
| WBTC | wrapped-bitcoin | wrapper |
| ETH | ethereum | crypto |
| WETH | weth | wrapper |
| stETH | staked-ether | lst |
| wstETH | wrapped-steth | lst |
| rETH | rocket-pool-eth | lst |
| SOL | solana | crypto |
| USDC | usd-coin | stablecoin |
| USDT | tether | stablecoin |
| DAI | dai | stablecoin |
| PYUSD | paypal-usd | stablecoin |

---

## API endpoints

| Method | Path | Description |
|---|---|---|
| GET | `/api/reference/assets` | Latest market snapshot for all tracked assets |
| GET | `/api/reference/assets/{symbol}` | Canonical metadata + current metrics for one asset |
| GET | `/api/reference/history/{symbol}` | Price / market cap / 24h volume time series |
| GET | `/api/reference/global` | Global crypto market context (live passthrough) |
| GET | `/api/reference/usage` | CoinGecko API key usage / credit balance (Pro only) |

Query params:
- `/assets?symbols=BTC&symbols=ETH` — filter to specific symbols
- `/history/{symbol}?days=90` — history depth (1–365)

---

## Database tables

| Table | Purpose | Retention |
|---|---|---|
| `asset_reference_map` | Canonical symbol ↔ coingecko_id mapping + metadata | Upserted daily |
| `market_reference_snapshots` | Periodic price/cap/volume snapshots | Append; no auto-purge |
| `market_reference_history` | Daily OHLCV-style history from backfill | Append; deduped by (coingecko_id, snapshot_at) |
| `api_usage_snapshots` | CoinGecko credit usage (Pro) | Append |

---

## Ingestion jobs

| Job | Interval | Function |
|---|---|---|
| Market snapshots | Every 15 min | `ingest_market_snapshots(db)` |
| API usage | Every 30 min | `ingest_api_usage(db)` — no-op if no Pro key |
| Asset map | Ad hoc / daily | `ingest_asset_map(db)` — triggered via `/api/admin/ingest` |
| History backfill | Ad hoc | `backfill_all(db, days=365)` — triggered via `/api/admin/backfill` |

All jobs are also triggered as part of the `/api/admin/ingest` call that runs on app open.

---

## Frontend integration

| Page | Component | Data used |
|---|---|---|
| Overview | `GlobalMarketCard` | Total market cap, 24h volume, BTC/ETH dominance, 24h change |
| Asset cockpit | `MarketContextCard` | Spot price, price change, market cap, 24h volume, FDV, supply bar, sparkline |

CoinGecko data is displayed with a "CoinGecko" source tag and is clearly distinguished from protocol-native lending or derivatives data.

---

## Design constraints

- Do not replace Velo for derivatives.
- Do not replace Aave / Morpho / Kamino for LTVs, caps, or borrow logic.
- Do not expose the API key to the browser.
- Mark all rows `source_name = 'coingecko'`.
- Retry on 429/5xx with tenacity exponential backoff (3 attempts, 1–10s).
- Free tier fallback when no API key is configured.
