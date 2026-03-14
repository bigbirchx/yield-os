# First prompts to run in Cursor

## Prompt 1 — scaffold the repo
Read docs/PROJECT_BRIEF.md and docs/ARCHITECTURE.md first.

Create a production-ready MVP monorepo for this Yield Cockpit project.

Requirements:
- apps/api = FastAPI
- apps/web = Next.js App Router
- Postgres and Redis via docker-compose
- Alembic migrations
- SQLAlchemy models
- health check endpoint
- environment variable handling
- clean folder structure with routers/services/repositories

Generate:
1. docker-compose.yml
2. backend FastAPI scaffold
3. frontend Next.js scaffold
4. initial Alembic migration
5. README with exact startup commands

Constraints:
- keep implementation MVP-simple
- do not add auth
- do not add unnecessary abstractions

## Prompt 2 — Velo connector
Read docs/PROJECT_BRIEF.md, docs/DATA_DICTIONARY.md, and docs/SOURCE_MAP.md first.

Implement a reusable Velo connector.

Requirements:
- env-based auth using VELO_API_KEY
- typed client
- support funding, open interest, spot/perp volume, basis, mark price, and index price
- normalize into derivatives_snapshot
- preserve raw payloads
- add retry logic, timeout handling, and structured logging
- add a scheduled ingestion job for BTC, ETH, and SOL every 5 minutes
- add GET /api/derivatives/overview
- add tests with mocked responses

Do not add auth or unrelated abstractions.

## Prompt 3 — DeFiLlama connector
Read docs/PROJECT_BRIEF.md, docs/DATA_DICTIONARY.md, and docs/SOURCE_MAP.md first.

Implement a DeFiLlama connector.

Requirements:
- env-based auth using DEFILLAMA_API_KEY
- ingest current lend/borrow data and historical lend/borrow chart data
- ingest staking/LSD rate data for MVP
- normalize into lending_market_snapshot and staking_snapshot
- preserve raw payloads
- support backfill jobs
- add GET /api/lending/overview
- add GET /api/assets/{symbol}/history
- add tests

Keep the implementation simple and traceable.

## Prompt 4 — overview page
Read docs/PROJECT_BRIEF.md first.

Build the /overview page.

Requirements:
- cards for top borrow rates, top lend rates, highest funding, highest basis, and largest capacity constraints
- source labels on each card
- data freshness timestamps
- desktop-first institutional styling
- use backend endpoints already created

## Prompt 5 — asset cockpit
Read docs/PROJECT_BRIEF.md and docs/DATA_DICTIONARY.md first.

Build /assets/[symbol].

Requirements:
- sections for lending, derivatives, staking, transforms, history, and events
- ECharts for time-series and basis charts
- TanStack Table for market tables
- include a placeholder explanation card for why borrow demand may be elevated
- show source tags and freshness labels

## Prompt 6 — direct protocol risk connectors
Read docs/SOURCE_MAP.md and docs/DATA_DICTIONARY.md first.

Implement direct connectors for Aave, Morpho, and Kamino.

Requirements:
- normalize max_ltv, liquidation_threshold, borrow caps, supply caps, collateral eligibility, and available capacity
- create protocol_risk_params_snapshot
- preserve raw payloads
- add POST /api/lending/ltv-matrix
- add tests

Do not invent fields that are not grounded in the source payloads.

## Prompt 7 — borrow-demand explainer
Read docs/PROJECT_BRIEF.md and docs/DATA_DICTIONARY.md first.

Build a borrow-demand explanation engine.

Inputs:
- funding history
- OI history
- basis history
- borrow rates
- cap headroom
- transform metadata
- event overlays

Outputs:
- structured reason scores
- confidence score
- concise 3-5 sentence explanation suitable for an institutional desk dashboard

Keep it factual, concise, and traceable to metrics.

## Prompt 8 — route optimizer
Read docs/PROJECT_BRIEF.md, docs/DATA_DICTIONARY.md, and docs/SOURCE_MAP.md first.

Create a route optimizer for sourcing a requested asset.

Compare:
1. direct borrow
2. stablecoin borrow then spot purchase
3. wrapper transform route
4. synthetic hedge route

Output:
- ranked routes
- annualized estimated source cost
- max executable size
- main bottlenecks
- short explanation for ranking

Keep assumptions explicit and preserve hooks for manual metadata later.

## Prompt 9 — internal exchange data layer (perps, funding, RV)
Read docs/PROJECT_BRIEF.md, docs/DATA_DICTIONARY.md, docs/SOURCE_MAP.md,
and docs/OTHER_WORKSPACE_BRIEF.md first.

docs/OTHER_WORKSPACE_BRIEF.md is a complete technical reference for an internal
production codebase at /home/ec2-user/workspace/ that contains live exchange
connectors and market data libraries. Use it as your sole guide for this prompt.
Do not explore /home/ec2-user/workspace/ beyond what the brief already documents.
Do not edit, modify, create, or delete any file under /home/ec2-user/workspace/.

Implement an internal exchange data layer that supplements the external API
connectors (Velo, DeFiLlama) with live and historical data sourced from the
internal codebase via sys.path injection as described in the brief.

Requirements:
- sys.path injection module at apps/api/app/connectors/internal/path_setup.py
  following the exact pattern in the brief
- typed client at apps/api/app/connectors/internal/exchange_client.py exposing:
    - get_funding_rate_history(base_ccy, exchange, day_count) → normalized DataFrame
    - get_current_funding_rate(base_ccy, exchange) → float (annualized)
    - get_xccy_funding_spread(base_ccy, quote_ccy, day_count) → DataFrame
    - get_perp_mark_price_ohlc(base_ccy, days_lookback) → DataFrame
    - get_realized_vol(base_ccy, day_counts) → DataFrame
    - get_market_metrics(base_ccy, exchange) → dict (OI, volume)
- normalize all outputs into the derivatives_snapshot schema already established
- all timestamps UTC-aware, funding rates annualized, consistent with existing connectors
- graceful fallback if internal paths are unavailable (log warning, return empty)
- add scheduled ingestion job for BTC, ETH, SOL every 5 minutes alongside Velo
- add GET /api/derivatives/funding/history?symbol=BTC&exchange=binance&days=365
- add GET /api/derivatives/funding/current?symbol=BTC
- add GET /api/derivatives/rv?symbol=BTC
- add tests with the internal calls mocked (do not call live exchange APIs in tests)

Constraints:
- all new files go exclusively inside /home/ec2-user/cdb_workspace/yield-os/
- never write to /home/ec2-user/workspace/ under any circumstance
- do not copy source files from the reference repo; import via sys.path only
- keep implementation MVP-simple and consistent with existing connector patterns

## Prompt 10 — multi-exchange funding rate dashboard
Read docs/PROJECT_BRIEF.md, docs/DATA_DICTIONARY.md, docs/SOURCE_MAP.md,
and docs/OTHER_WORKSPACE_BRIEF.md first.

docs/OTHER_WORKSPACE_BRIEF.md documents the internal reference codebase.
Use it for all data access patterns. Do not edit anything under
/home/ec2-user/workspace/.

Implement a multi-exchange perpetual funding rate dashboard as a new page
at apps/web/app/funding/page.tsx backed by a new FastAPI router at
apps/api/app/routers/funding_snapshot.py.

---

## Data layer — what to build vs. what to wire up

The following exchange connectors already exist in the reference repo and must
be imported via sys.path injection (see OTHER_WORKSPACE_BRIEF.md section 2):

  Binance  — full history (3 years) + live predicted rate + OI + volume
             via PerpFuture._get_binance_funding_rate_history()
             and migration/lib/binance_funcs.get_binance_predicted_funding_rate()
             and migration/lib/binance_funcs.get_binance_market_metrics()

  OKX      — full history (paginated) + live rate
             via PerpFuture._get_okx_funding_rate_history()
             and migration/lib/okx_funcs.get_okx_funding_rate()

  Bybit    — full history (cursor-paginated) + live mark price
             via PerpFuture._get_bybit_funding_rate_history()
             Live current rate must be fetched directly from:
             GET https://api.bybit.com/v5/market/tickers?category=linear&symbol={symbol}
             field: fundingRate (current period rate, not yet settled)

  Deribit  — history via interest_1h field, annualized as rate * 365 * 24
             via PerpFuture._get_deribit_funding_rate_history()
             Live rate from: GET https://www.deribit.com/api/v2/public/ticker
             field: funding_8h (annualize as * 3 for daily * 365 for annual)
             OI from: PerpFuture._get_deribit_open_interest()

  Bullish  — live funding rates via Bullish(public_key, private_key).get_funding_rates()
             Historical funding via get_derivatives_settlement_history() (90-day window)
             Credentials from .env: BULLISH_PUBLIC_KEY, BULLISH_PRIVATE_KEY,
             BULLISH_API_HOSTNAME, BULLISH_OPTIONS_MM_ACCOUNT_ID
             Bullish class lives at:
             /home/ec2-user/workspace/exodus/analytics_frontend/streamlit/bullish.py
             Add its parent dir to sys.path alongside the other injected paths.

The following does NOT exist in the reference repo and must be built from scratch:
  - Coinglass connector (add as a secondary/fallback source)
    Public endpoint (no auth required for basic data):
    GET https://open-api.coinglass.com/public/v2/funding
    Use COINGLASS_API_KEY from environment for rate-limited endpoints.
    Map response fields: symbol, uFundingRate (Binance), oFundingRate (OKX),
    bybitFundingRate, dydxFundingRate. Use as a cross-check / secondary display only.
  - Blended rate logic (build fresh, described below)
  - Exchange Funding Snapshot table (build fresh, described below)

---

## Backend — apps/api/app/routers/funding_snapshot.py

Create GET /api/funding/snapshot?symbol=BTC

Response shape:
{
  "symbol": "BTC",
  "as_of": "<ISO timestamp>",
  "exchanges": {
    "binance":  { "live_apr": float, "last_apr": float, "funding_interval_hours": float,
                  "oi_coin": float, "oi_usd": float, "volume_coin_24h": float,
                  "ma_7d_apr": float, "ma_30d_apr": float },
    "okx":      { same shape },
    "bybit":    { same shape },
    "deribit":  { same shape },
    "bullish":  { live_apr, last_apr, funding_interval_hours only — no OI/volume }
  },
  "blended": {
    "equal_weighted_apr": float,
    "oi_weighted_apr": float,
    "volume_weighted_apr": float
  },
  "coinglass": { "binance_apr": float, "okx_apr": float, "bybit_apr": float }
}

Blending logic:
- Fetch live_apr and oi_usd / volume_coin_24h per exchange
- Equal weighted: mean of available live_apr values
- OI weighted: sum(live_apr_i * oi_usd_i) / sum(oi_usd_i) — skip exchanges with no OI
- Volume weighted: sum(live_apr_i * volume_coin_24h_i) / sum(volume_coin_24h_i)
- Exclude any exchange from a weighted blend if its weight metric is 0 or unavailable
- funding_interval_hours: derive from the time delta between the last two funding events
  in the history DataFrame (diff of timestamps)

For ma_7d_apr and ma_30d_apr:
- Fetch history via the reference repo connectors
- Resample to daily mean of annualized_funding_rate
- Compute rolling(7).mean() and rolling(30).mean(), take the latest value
- Cache results for 5 minutes (TTL cache or Redis)

---

## Backend — apps/api/app/routers/funding_history.py

Create GET /api/funding/history?symbol=BTC&exchange=binance&days=365&blend=false

When blend=false: return raw annualized_funding_rate time series for one exchange.
When blend=true: fetch all available exchanges, resample to daily mean per exchange,
align timestamps, compute all three blended series (equal, OI-weighted, volume-weighted)
and return all series in one response.

Response shape:
{
  "symbol": "BTC",
  "exchange": "blended" | exchange name,
  "series": [{ "date": "YYYY-MM-DD", "value": float }, ...],
  "blend_series"?: {
    "equal_weighted": [...],
    "oi_weighted": [...],
    "volume_weighted": [...]
  }
}

---

## Frontend — apps/web/app/funding/page.tsx

Build a full-width funding rate dashboard page.

Section 1 — Exchange Funding Snapshot table (top of page)
Columns: Exchange | Live APR | Last APR | 7d MA APR | 30d MA APR | OI (USD) |
         Funding Interval (hrs) | Volume Coin 24h
Rows: Binance, OKX, Bybit, Deribit, Bullish, Blended (all three variants as sub-rows)
- Color-code APR cells: green for positive, red for negative
- Source: GET /api/funding/snapshot

Section 2 — Controls
- Symbol selector (BTC, ETH, SOL default)
- Exchange checkboxes: Binance, OKX, Bybit, Deribit, Bullish (default all checked)
- Blend toggle: off / equal-weighted / OI-weighted / volume-weighted
- Days of history slider (30, 90, 180, 365)
- Moving average periods: three configurable inputs (default 7, 30, 90 days)

Section 3 — Time-series chart (ECharts)
- Plot daily annualized funding rate for each selected exchange as separate lines
- When blend is active, show the blended series as a bold overlay line
- Show the three configurable MA lines for the selected/blended series
- Tooltip shows all exchange values at the hovered date

Section 4 — Distribution analysis (below chart)
- Histogram + KDE overlay for each MA period
- KDE bandwidth slider (0.05–1.5, default 0.4)
- Percentile slider with annotation on histogram
- KDE percentile table (5th, 25th, 50th, 75th, 95th) per MA period
- Box plot comparing the three MA period distributions

Section 5 — Coinglass cross-check strip
- Small row of badges showing Coinglass-sourced live rates for Binance, OKX, Bybit
- Labeled "Source: Coinglass" with freshness timestamp
- Show delta vs. internal rate for each exchange

---

## Constraints
- All new files go exclusively inside /home/ec2-user/cdb_workspace/yield-os/
- Never write to /home/ec2-user/workspace/ under any circumstance
- Import reference connectors via sys.path injection only — do not copy source files
- Bullish OI and volume are not available from the API; show "—" in those cells
- Deribit funding is USD-settled (not USDT); note this in the tooltip
- OKX and Bybit OI endpoints are not implemented in perps.py (_get_okx_open_interest
  and _get_bybit_open_interest raise NotImplementedError) — call their REST APIs
  directly for those fields:
    Bybit OI: GET https://api.bybit.com/v5/market/tickers?category=linear&symbol=BTCUSDT
              field: openInterest (coin), openInterestValue (USD)
    OKX OI:   GET https://www.okx.com/api/v5/public/open-interest?instId=BTC-USDT-SWAP
              field: oiCcy (coin), oiUsd (USD)
- Add tests with all external API calls mocked
- Keep implementation consistent with the connector and endpoint patterns already
  established for Velo and DeFiLlama

  ## Prompt 11 - CoinGecko Integration
  Read these files first:
- docs/PROJECT_BRIEF.md
- docs/DATA_DICTIONARY.md
- docs/SOURCE_MAP.md
- docs/GIT_WORKFLOW.md

We are adding CoinGecko Pro API to Yield OS in a way that is useful for an institutional crypto yield cockpit.

High-level intent:
Use CoinGecko as a MARKET REFERENCE and ASSET METADATA layer, not as the primary source for protocol-native lending parameters or derivatives structure.
Keep Velo as the primary derivatives source.
Keep DeFiLlama and protocol-native connectors as the primary DeFi/protocol source.
Use CoinGecko to improve:
1. canonical asset mapping
2. spot price history
3. market cap / volume context
4. token metadata enrichment
5. global market overview cards
6. internal API usage monitoring

Implementation requirements:

1) Environment variables
Add support for:
- COINGECKO_API_KEY
- COINGECKO_BASE_URL

Defaults:
- COINGECKO_BASE_URL should default to https://pro-api.coingecko.com/api/v3

Use backend-only requests.
Pass the API key via the x-cg-pro-api-key header.
Do not expose the API key to the frontend.

2) Build a CoinGecko client
Create a reusable backend client with:
- typed request/response models where practical
- timeout handling
- retry logic with backoff for transient failures
- structured logging
- graceful handling of rate limit / auth errors
- helper methods for:
  - ping / health check
  - /coins/list
  - /coins/markets
  - /coins/{id}/market_chart
  - /coins/{id}/market_chart/range
  - /global
  - /onchain/networks/{network}/tokens/multi/{addresses}
  - /key

3) Database design
Add the minimum new tables needed for an MVP-quality integration.

Suggested tables:
- asset_reference_map
  - symbol
  - canonical_symbol
  - coingecko_id
  - name
  - asset_type
  - chain
  - contract_address
  - source_name
  - raw
- market_reference_snapshot
  - ts
  - coingecko_id
  - symbol
  - current_price_usd
  - market_cap_usd
  - fully_diluted_valuation_usd
  - volume_24h_usd
  - circulating_supply
  - total_supply
  - max_supply
  - price_change_24h_pct
  - source_name
  - raw
- market_reference_history
  - ts
  - coingecko_id
  - price_usd
  - market_cap_usd
  - volume_24h_usd
  - source_name
  - raw
- api_usage_snapshot
  - ts
  - provider
  - rate_limit
  - remaining_credits
  - monthly_total_credits
  - raw

Preserve raw payloads in JSON columns.
Keep schema naming consistent with the existing repo style.

4) Ingestion jobs
Implement jobs for:
- daily / ad hoc asset discovery and mapping refresh from /coins/list
- periodic market reference snapshots from /coins/markets for our tracked assets
- historical backfill from /coins/{id}/market_chart or /market_chart/range
- periodic API usage snapshots from /key
- optional token enrichment by contract address for wrapper/LST/stablecoin assets

Tracked assets for MVP:
- BTC family
- ETH family
- SOL family
- major USD stablecoin family
- key wrappers / LSTs relevant to Yield OS

5) API endpoints
Add these endpoints:
- GET /api/reference/assets
- GET /api/reference/assets/{symbol}
- GET /api/reference/history/{symbol}
- GET /api/reference/global
- GET /api/reference/usage

Behavior:
- /api/reference/assets returns current market reference data for tracked assets
- /api/reference/assets/{symbol} returns canonical asset metadata and current reference metrics
- /api/reference/history/{symbol} returns historical price / market cap / 24h volume time series
- /api/reference/global returns high-level crypto market context
- /api/reference/usage returns CoinGecko API usage/health information

6) Frontend integration
Integrate CoinGecko data into the app only where it materially improves decision-making.

Use cases:
- asset cockpit:
  - show CoinGecko spot price history
  - show market cap and 24h volume context
  - show canonical asset metadata / images if appropriate
- market overview:
  - add global market context card
  - optionally add top market-cap context for tracked assets
- route / quote logic:
  - use CoinGecko only as a reference pricing and metadata layer
  - do not replace protocol-native liquidity / cap / collateral / borrow logic

7) Product constraints
- Do not replace Velo for derivatives.
- Do not replace Aave / Morpho / Kamino / protocol-native data for LTVs, caps, or quote-critical borrowing constraints.
- Do not expose the CoinGecko API key to the browser.
- Do not add unnecessary abstraction.
- Keep implementation MVP-simple and maintainable.
- Preserve source labels and freshness timestamps in UI.
- Mark CoinGecko as source_name = 'coingecko'.

8) Quality requirements
- add tests with mocked CoinGecko responses
- add migration(s) for new tables
- add repository/service separation consistent with repo conventions
- include a short README update describing:
  - env vars
  - endpoints
  - ingestion jobs
  - intended use of CoinGecko in Yield OS

9) Git workflow
Follow docs/GIT_WORKFLOW.md.
When complete:
- stage only relevant files
- create a conventional commit
- include Task / Files / Reason / Tests in the commit body
- report the commit hash

Deliver:
1. exact files changed
2. migrations added
3. new env vars
4. new endpoints
5. tests added
6. concise explanation of design choices

Important product judgment:
CoinGecko should serve as the app’s canonical market reference + asset metadata layer.
It should improve asset normalization, historical spot context, and market-cap/volume context.
It should not become the source of truth for protocol-level yield, lending risk, or derivatives routing.

## Prompt 12 — dated futures basis curve
Read docs/PROJECT_BRIEF.md, docs/DATA_DICTIONARY.md, docs/SOURCE_MAP.md,
and docs/OTHER_WORKSPACE_BRIEF.md first.

docs/OTHER_WORKSPACE_BRIEF.md documents the internal reference codebase.
Use it for all data access patterns. Do not edit anything under
/home/ec2-user/workspace/.

Implement a dated futures basis dashboard as a new page at
apps/web/app/basis/page.tsx backed by a new FastAPI router at
apps/api/app/routers/basis.py.

---

## Data layer — what to wire up vs. what to build fresh

### Deribit (reference repo — inject via sys.path)

The following functions in the reference repo are ready to use:

  /home/ec2-user/workspace/exodus/api_wrappers/ad_derivs_funcs.py

  get_listed_basis_history(ccy, expiry_date, lookback_days=89)
    - Returns hourly time-series for one Deribit expiry
    - Fields: timestamp (index), basis_USD, basis_%_term, basis_%_annualized,
              daysToExpiration, underlyingPrice, indexPrice, openInterest
    - Hard limit: lookback_days must be < 90 (endpoint uses hourly timeInterval)
    - Requires AD_DERIVS_KEY from AWS Secrets Manager:
        get_secret("amberdata")["api_key"]

  get_intraday_basis_run(ccy, expiry_str)
    - Last 11 hours, minutely. Fields: basis_usd, basis_pct_ann, indexPrice,
      underlyingPrice, openInterest, expirationTimestamp, daysToExpiration

  To get all active Deribit expiries for a currency, call the Amberdata
  delta surfaces endpoint that ad_derivs_funcs already uses, or adapt
  get_ad_listed_expiry_term_structure() to extract expiry dates.

  Build a wrapper that fetches all active Deribit expiries concurrently
  (ThreadPoolExecutor, pattern already in vol_term_structure_run()) and
  returns the full term structure snapshot.

### Binance / OKX / Bybit / CME dated futures (build fresh)

These are NOT in the reference repo. Build direct REST connectors:

  Binance dated futures (USDT-margined):
    Enumerate active dated contracts:
      GET https://fapi.binance.com/fapi/v1/exchangeInfo
      Filter: contractType in ['CURRENT_QUARTER', 'NEXT_QUARTER']
    Mark price (contains basis components):
      GET https://fapi.binance.com/fapi/v1/premiumIndex?symbol=BTCUSDT_250328
      Fields: symbol, markPrice, indexPrice, lastFundingRate, nextFundingTime
    OHLCV: GET https://fapi.binance.com/fapi/v1/klines?symbol=BTCUSDT_250328&interval=1d
    Open interest: GET https://fapi.binance.com/fapi/v1/openInterest?symbol=BTCUSDT_250328
    24h volume: GET https://fapi.binance.com/fapi/v1/ticker/24hr?symbol=BTCUSDT_250328

  OKX dated futures:
    Enumerate active contracts:
      GET https://www.okx.com/api/v5/public/instruments?instType=FUTURES
      Filter: state=live, ctType=linear, quoteCcy=USDT
    Mark price:
      GET https://www.okx.com/api/v5/public/mark-price?instId=BTC-USDT-250328&instType=FUTURES
    OHLCV: GET https://www.okx.com/api/v5/market/candles?instId=BTC-USDT-250328&bar=1D
    Open interest: GET https://www.okx.com/api/v5/public/open-interest?instId=BTC-USDT-250328
    24h ticker: GET https://www.okx.com/api/v5/market/ticker?instId=BTC-USDT-250328

  Bybit dated futures:
    Enumerate: GET https://api.bybit.com/v5/market/instruments-info?category=linear
    Filter: contractType=LinearFutures, status=Trading
    Ticker (mark price, index, OI, volume):
      GET https://api.bybit.com/v5/market/tickers?category=linear&symbol=BTCUSDT-28MAR25

  CME Bitcoin futures (attempt with existing Amberdata key first):
    GET https://api.amberdata.com/markets/futures/ohlcv/BTCUSD_250328
        ?exchange=cme&timeInterval=days
    Headers: x-api-key: <amberdata_derivs key>
    If the key lacks CME data subscription, log a warning and return empty
    rather than failing. CME is lower priority — do not block other venues.

---

## Basis calculation (consistent formula across all venues)

  index_price = spot index price (not mark price)
  futures_price = mark price of the dated futures contract
  days_to_expiry = (expiry_datetime - now).days  (floor to 0 minimum)

  basis_usd       = futures_price - index_price
  basis_pct_term  = basis_usd / index_price
  basis_pct_ann   = basis_pct_term * (365 / days_to_expiry)   # undefined if DTE <= 0

All annualized basis values stored and returned as decimals (0.05 = 5%).

---

## Backend — GET /api/basis/snapshot?symbol=BTC

Response shape:
{
  "symbol": "BTC",
  "as_of": "<ISO timestamp>",
  "term_structure": [
    {
      "venue": "deribit" | "binance" | "okx" | "bybit" | "cme",
      "contract": "BTC-28MAR25",
      "expiry": "2025-03-28T08:00:00Z",
      "days_to_expiry": 14,
      "futures_price": 85000.0,
      "index_price": 84000.0,
      "basis_usd": 1000.0,
      "basis_pct_ann": 0.158,
      "oi_coin": 12500.0,
      "oi_usd": 1050000000.0,
      "volume_24h_usd": 450000000.0
    },
    ...
  ]
}

Sort by days_to_expiry ascending. Include all venues and expiries.
Perpétuals (DTE = null / infinite) are excluded from this endpoint —
they belong in the funding snapshot endpoint already built.

---

## Backend — GET /api/basis/history?symbol=BTC&venue=deribit&contract=BTC-28MAR25&days=89

Response shape:
{
  "symbol": "BTC",
  "venue": "deribit",
  "contract": "BTC-28MAR25",
  "expiry": "2025-03-28T08:00:00Z",
  "series": [
    { "timestamp": "...", "basis_usd": float, "basis_pct_ann": float,
      "futures_price": float, "index_price": float, "days_to_expiry": float }
  ]
}

For Deribit: use get_listed_basis_history() from reference repo (max 89 days).
For other venues: construct from daily OHLCV klines. Use close price as futures_price.
Index price for Binance: fetch from premiumIndex endpoint at each candle timestamp
  (note: this is not stored in kline data — use a daily snapshot approach).

---

## Frontend — apps/web/app/basis/page.tsx

Section 1 — Basis Term Structure Chart (primary)
- X-axis: days to expiry (0 to max DTE)
- Y-axis: annualized basis %
- One plotted point per active contract, one series per venue
- Hover tooltip: contract name, expiry date, basis USD, OI USD, 24h volume
- Separate series per venue with distinct colors
- Use ECharts scatter + line chart

Section 2 — Basis Snapshot Table
Columns: Venue | Contract | Expiry | DTE | Futures Price | Index Price |
         Basis USD | Basis Ann % | OI (USD) | 24h Volume (USD)
Sort: DTE ascending, then venue
Color-code Basis Ann %: gradient from blue (low) to red (high)

Section 3 — Historical Basis Chart (selected contract)
- Click a row in the snapshot table to load history for that contract
- Time-series of basis_pct_ann over available history
- Source: GET /api/basis/history

Section 4 — Controls
- Symbol selector (BTC, ETH default)
- Venue checkboxes (Deribit, Binance, OKX, Bybit, CME)
- Toggle: show in USD basis vs. annualized %

---

## Constraints
- All new files go exclusively inside /home/ec2-user/cdb_workspace/yield-os/
- Never write to /home/ec2-user/workspace/ under any circumstance
- Import reference repo functions via sys.path injection only
- If CME Amberdata subscription is unavailable, log a warning and omit CME
  from the response — do not throw a 500 error
- DTE = 0 or negative: exclude from term structure (contract has expired)
- Add tests with all exchange REST calls mocked
- Keep consistent with the connector patterns already established

---

## Prompt 13 — Bug fixes: funding history, Deribit basis, overview data (2026-03-14)

### Issues addressed
1. Overview page showed "no funding data — run ingestion first" for all derivatives sections
2. Funding rate history charts showed no data for any exchange
3. Deribit was absent from the Basis term structure dashboard
4. Basis term structure was scatter-only (no line connecting venue dots)

### Root cause
The Docker API container had no access to `/home/ec2-user/workspace/` at runtime,
so `_HAS_APIS = False` and all internal connector paths (MongoDB history,
PerpFuture class, ad_derivs_funcs) were silently disabled. The service fell
back to empty DataFrames / empty lists for every exchange.

### Changes

**`apps/api/app/services/funding_service.py`**
- Added `_binance_rest_history`, `_okx_rest_history`, `_bybit_rest_history`,
  `_deribit_rest_history` — public REST fallbacks using Binance FAPI,
  OKX, Bybit v5, and Deribit public history endpoints respectively.
- Fixed Deribit field name: `interest_8h` (not `interest`).
- Modified `_mongo_history` to attempt MongoDB first then fall back to REST.
- Modified `_fetch_binance._live()`, `_fetch_okx._live()`, `_fetch_bybit._history()`,
  `_fetch_deribit._history()` / `._oi()` to use REST fallbacks when reference
  codebase is unavailable.
- Modified `get_funding_history` for bybit/deribit to use REST fallbacks.

**`apps/api/app/services/basis_service.py`**
- Removed `sys.path` injection + `_HAS_DERIBIT` entirely.
- Replaced `_fetch_deribit_snapshot` with direct Deribit public REST:
  `get_book_summary_by_currency` for mark prices + `get_index_price` for the
  BTC/ETH index. No API key required.
- Replaced `_deribit_history` with direct Deribit `get_tradingview_chart_data`
  for both the futures contract and the price index (daily resolution).
- Fixed CME functions to look up `settings.amberdata_derivs_key` (new config
  field) instead of a module-level `_AD_DERIVS_KEY`.

**`apps/api/app/core/config.py`**
- Added `amberdata_derivs_key: str = ""` setting for CME futures.

**`apps/api/app/connectors/internal/exchange_client.py`**
- `get_current_funding_rate`: tries reference codebase first, then falls back
  to Binance FAPI `premiumIndex` / OKX `funding-rate` REST.
- `get_market_metrics`: tries reference codebase first, then falls back to
  Binance `openInterest` / OKX `open-interest` REST.

**`apps/api/app/services/internal_ingestion.py`**
- Removed `if not _HAS_APIS: return {}` gate; REST fallbacks now populate
  `derivatives_snapshots` even without the reference codebase.

**`apps/api/app/routers/admin.py`**
- `POST /api/admin/ingest` now also calls `internal_ingest_all` so
  derivatives snapshot data (used by the overview page) is written on demand.

**`apps/web/src/app/basis/page.tsx`**
- Upgraded term structure chart from plain scatter to line+scatter:
  dashed line per venue connects the dots; scatter dots remain for tooltips.
  Legend shows only the line series (no duplicate entries).

**`apps/web/src/app/overview/page.tsx`**
- Updated empty state messages to be informative and point to the dedicated
  Funding and Basis pages instead of saying "run ingestion first".

**`docker-compose.yml`**
- Added `/home/ec2-user/workspace:/home/ec2-user/workspace:ro` volume mount
  to the API service so reference codebase imports succeed when present.

### Verification
- `GET /api/basis/snapshot?symbol=BTC` → venues: deribit (6), binance (2), bybit (8)
- `GET /api/funding/history?exchange=binance` → 30 points
- `GET /api/funding/history?exchange=bybit` → 30 points
- `GET /api/funding/history?exchange=okx` → 34 points
- `GET /api/funding/history?exchange=deribit` → 31 points
- `POST /api/admin/ingest` populates derivatives_snapshots; overview page shows
  Binance + OKX funding rates

### Commit
`5e03e65` — fix(api,web): direct REST fallbacks + Deribit basis + live ingestion
