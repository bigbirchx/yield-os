# Yield Cockpit

Institutional crypto yield monitoring — MVP monorepo.

## Stack

| Layer     | Technology                       |
|-----------|----------------------------------|
| Backend   | FastAPI + SQLAlchemy + Alembic   |
| Frontend  | Next.js 15 (App Router)          |
| Database  | PostgreSQL 16                    |
| Cache     | Redis 7                          |
| Infra     | Docker Compose                   |

## Repository layout

```
apps/
  api/          FastAPI backend
    app/
      core/           config, database session
      models/         SQLAlchemy ORM models
      routers/        HTTP route handlers (admin, basis, borrow_demand,
                      derivatives, funding, lending, reference, staking)
      services/       business logic (basis_service, funding_service,
                      internal_ingestion, defillama_ingestion, etc.)
      connectors/     external data connectors
        internal/     internal exchange REST wrappers (Binance, OKX, Bybit)
        coinglass_client.py
    alembic/    migration scripts
    tests/
  web/          Next.js frontend
    src/app/    App Router pages:
                  /overview, /funding, /basis, /assets/[symbol]
    src/lib/    API client helpers (api.ts)
    src/components/  shared UI components
  worker/       (future) standalone ingestion jobs
docs/           project docs — ARCHITECTURE, DATA_DICTIONARY, SOURCE_MAP, etc.
```

## Pages

| Page | Path | Description |
|------|------|-------------|
| Market Overview | `/overview` | Top borrow/lend rates, highest funding & basis, capacity constraints |
| Funding Rates | `/funding` | Live perpetual funding rates + 90-day history charts (Binance, OKX, Bybit, Deribit) |
| Dated Futures Basis | `/basis` | Basis term structure chart, snapshot table, and historical basis per venue (Binance, OKX, Bybit, Deribit) |
| Asset Cockpit | `/assets/[symbol]` | Per-asset rates, borrow demand, risk params, route optimizer |

## API endpoints (selected)

| Endpoint | Description |
|----------|-------------|
| `GET /api/health` | Service health check |
| `GET /api/funding/snapshot?symbol=BTC` | Live perpetual funding snapshot all venues |
| `GET /api/funding/history?symbol=BTC&exchange=binance&days=90` | Funding rate history |
| `GET /api/basis/snapshot?symbol=BTC` | Dated futures basis term structure |
| `GET /api/basis/history?symbol=BTC&venue=deribit&contract=BTC-28MAR25&days=89` | Historical basis for a contract |
| `GET /api/derivatives/overview?symbols=BTC&symbols=ETH` | Derivatives overview (OI, funding, basis) |
| `GET /api/lending/overview?symbols=USDC` | Lending rates overview |
| `POST /api/admin/ingest` | Trigger full data ingestion (DeFiLlama, Aave, Morpho, Kamino, internal exchanges) |
| `POST /api/admin/backfill?days=90` | Backfill historical lending data |

## Prerequisites

- Docker and Docker Compose v2
- `.env` file in repo root (copy from `.env.example`)
- The server running Docker must have `/home/ec2-user/workspace` available if reference codebase features are needed (mounted read-only automatically)

## Quick start

```bash
# 1. copy env file
cp .env.example .env
# edit .env — minimum: POSTGRES_PORT=5433 to avoid conflicts with host Postgres

# 2. start all services
docker compose up --build -d

# 3. trigger initial data ingestion
curl -X POST http://localhost:8000/api/admin/ingest

# 4. open the dashboard
open http://localhost:3000
```

> **Remote server access**: If running on a remote EC2 instance, use SSH port forwarding:
> ```bash
> ssh -L 3000:localhost:3000 -L 8000:localhost:8000 -N ec2-user@<HOST_IP>
> ```
> Then open `http://localhost:3000` locally.

Services after startup:

| Service    | URL                              |
|------------|----------------------------------|
| API docs   | http://localhost:8000/docs       |
| Health     | http://localhost:8000/api/health |
| Frontend   | http://localhost:3000            |

## Development (without Docker)

### Backend

```bash
cd apps/api
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# start postgres and redis separately, then:
uvicorn app.main:app --reload --port 8000
```

### Run migrations

```bash
cd apps/api
alembic upgrade head
```

### Backend tests

```bash
cd apps/api
pyenv shell 3.11.3
pytest
```

### Frontend

```bash
cd apps/web
npm install
npm run dev
```

## Environment variables

See `.env.example` for the full list. Key variables:

| Variable                | Description                                          |
|-------------------------|------------------------------------------------------|
| `POSTGRES_*`            | Database connection settings (use PORT=5433 on EC2)  |
| `REDIS_URL`             | Redis connection string                              |
| `VELO_API_KEY`          | Velo derivatives data API key (optional)             |
| `DEFILLAMA_API_KEY`     | DeFiLlama yield/lending API key (optional)           |
| `COINGECKO_API_KEY`     | CoinGecko Pro key (unlocks bulk endpoints)           |
| `COINGLASS_API_KEY`     | Coinglass secondary funding cross-check (optional)   |
| `AMBERDATA_DERIVS_KEY`  | Amberdata derivatives key (CME futures only)         |
| `BULLISH_PUBLIC_KEY`    | Bullish exchange public key (optional)               |
| `BULLISH_PRIVATE_KEY`   | Bullish exchange private key (optional)              |
| `LOG_LEVEL`             | API log level (default: INFO)                        |

## Data sources

All **core data** works with zero API keys via direct public REST:

| Venue / Source | Data | Key Required? |
|---------------|------|---------------|
| Binance FAPI  | Perp funding, OI, dated futures basis | No |
| OKX           | Perp funding, OI, dated futures basis | No |
| Bybit         | Perp funding, OI, dated futures basis | No |
| Deribit       | Perp funding, dated futures basis (public REST) | No |
| DeFiLlama     | Lending rates, TVL, staking yields | No |
| Aave GraphQL  | Reserve params, APY history, caps | No |
| Morpho GraphQL| Vault/market data | No |
| Kamino REST   | Solana lending markets | No |
| CME (Amberdata) | CME Bitcoin futures basis | Yes (`AMBERDATA_DERIVS_KEY`) |
| Velo          | Broader derivatives history (3y) | Yes (`VELO_API_KEY`) |
| CoinGecko Pro | Market caps, prices, global stats | Yes (`COINGECKO_API_KEY`) |

## DefiLlama free-tier integration

**Base URL**: `https://api.llama.fi` (main) · `https://yields.llama.fi` (pools) · `https://stablecoins.llama.fi` (stables)  
**No API key required.** No `pro-api.llama.fi` endpoints are used.

### Free endpoints used

| Endpoint | Purpose |
|----------|---------|
| `yields.llama.fi/pools` | Yield pool snapshots |
| `yields.llama.fi/chart/{pool_id}` | Daily APY/TVL history |
| `api.llama.fi/protocols` | Protocol TVL list |
| `api.llama.fi/protocol/{slug}` | Protocol detail + TVL breakdown |
| `api.llama.fi/tvl/{slug}` | Single protocol TVL scalar |
| `api.llama.fi/v2/chains` | Chain TVL snapshot |
| `api.llama.fi/v2/historicalChainTvl/{chain}` | Daily chain TVL history |
| `api.llama.fi/overview/dexs` | DEX volume overview |
| `api.llama.fi/overview/open-interest` | Perp open-interest overview |
| `api.llama.fi/overview/fees` | Fees & revenue overview |
| `api.llama.fi/summary/dexs/{protocol}` | Single DEX volume detail |
| `stablecoins.llama.fi/stablecoins` | Stablecoin supply snapshot |
| `stablecoins.llama.fi/stablecoincharts/all` | Aggregate daily circulating history |
| `stablecoins.llama.fi/stablecoin/{id}` | Single stablecoin detail |

### Intentionally excluded (Pro-only)

`/yields/poolsBorrow`, `/yields/chartLendBorrow`, `/yields/lsdRates`, `/yields/perps`,
unlocks, token liquidity, active users — all require `pro-api.llama.fi`.

### API endpoints added

| Endpoint | Description |
|----------|-------------|
| `GET /api/defillama/yields` | Filtered yield pools by symbol/chain/project |
| `GET /api/defillama/yields/{pool_id}/history` | Daily APY/TVL history for one pool |
| `GET /api/defillama/protocols` | Protocol TVL snapshots |
| `GET /api/defillama/protocols/{slug}` | Protocol detail (live) |
| `GET /api/defillama/chains` | Chain TVL snapshot + history |
| `GET /api/defillama/stablecoins` | Stablecoin supply context |
| `GET /api/defillama/stablecoins/{id}` | Single stablecoin detail |
| `GET /api/defillama/market-context` | DEX volume, OI, fees summaries |

### DB tables added (migration 005)

`defillama_yield_pool_snapshot`, `defillama_yield_pool_history`,
`defillama_protocol_snapshot`, `defillama_chain_tvl_history`,
`defillama_stablecoin_snapshot`, `defillama_stablecoin_history`,
`defillama_market_context_snapshot`

---

## Automatic data refresh

On page load, the frontend triggers `POST /api/admin/refresh` to ensure live
data is always shown. The API also runs scheduled background jobs:
- Every 15 min: DeFiLlama lending/staking pool snapshots
- Every 4 h: Extended DefiLlama pipeline (protocols, chains, stablecoins, market context)
- Every 5–15 min: All other exchange and reference data
