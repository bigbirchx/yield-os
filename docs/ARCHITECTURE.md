# Architecture

## Apps

- `apps/api` — FastAPI backend (Python 3.11)
- `apps/web` — Next.js 15 frontend (TypeScript, App Router)
- `apps/worker` — (planned) standalone ingestion jobs

## Database

- PostgreSQL 16 for normalized storage
- Raw JSON payloads preserved in snapshot tables (`raw_payload` JSONB)
- Alembic for schema migrations
- Redis 7 for in-process caching of exchange API responses (TTL 60–300 s)

## Key tables

| Table | Purpose |
|-------|---------|
| `derivatives_snapshots` | Per-venue perpetual funding + OI snapshots (5-min cadence) |
| `lending_market_snapshots` | Per-protocol/asset lending rates + caps (15-min cadence) |
| `lending_market_history` | Daily historical borrow/lend APY per pool |
| `risk_params` | Collateral parameters from Aave / Morpho / Kamino |
| `coingecko_market_snapshots` | Asset price, market cap, 24h volume |

## API router map

| Router | Prefix | Description |
|--------|--------|-------------|
| `health` | `/api/health` | Service liveness |
| `admin` | `/api/admin` | Ingest triggers, backfill, source status |
| `derivatives` | `/api/derivatives` | Perp OI/funding overview, per-asset summary |
| `funding` | `/api/funding` | Live snapshot + history (all venues) |
| `basis` | `/api/basis` | Dated futures term structure + history |
| `lending` | `/api/lending` | Borrow/lend rate overview + per-asset |
| `staking` | `/api/staking` | LST/staking yields |
| `borrow_demand` | `/api/borrow-demand` | Borrow demand explainer |
| `reference` | `/api/reference` | Reference rates (global market, asset metadata) |

## Main data flow

```
External APIs (REST/GraphQL)
        │
        ▼
  Connectors (apps/api/app/connectors/)
        │
        ▼
  Services (apps/api/app/services/)
    ├── Normalization (basis_usd, annualized rates, dte)
    ├── Caching (in-memory TTL + Redis)
    └── Persistence (SQLAlchemy → Postgres)
        │
        ▼
  Routers (apps/api/app/routers/)
    └── Pydantic response models → JSON
        │
        ▼
  Frontend (apps/web/src/)
    ├── Server Components (overview, asset pages)
    └── Client Components (funding chart, basis chart, data status bar)
```

## Exchange connector strategy

All core data uses **direct public REST** — no API keys required for the base
product. Higher-fidelity or deeper-history sources layer on top:

```
Tier 0 — Public REST (always available, no key)
  Binance FAPI, OKX, Bybit, Deribit public endpoints

Tier 1 — Key-gated extras
  CoinGecko Pro (bulk price data)
  Velo (3-year derivatives history)
  Amberdata Derivatives (CME futures)

Tier 2 — Internal reference codebase (mounted at /home/ec2-user/workspace)
  MongoDB-backed 3-year funding history (Binance, OKX)
  PerpFuture class (Bybit/Deribit extended history)
  — gracefully disabled when path not mounted in Docker
```

## Deribit basis implementation

Deribit dated futures basis uses **Deribit's own public API** (no key):
- Snapshot: `GET /api/v2/public/get_book_summary_by_currency?currency=BTC&kind=future`
  + `GET /api/v2/public/get_index_price?index_name=btc_usd`
- History: `GET /api/v2/public/get_tradingview_chart_data?instrument_name=BTC-28MAR25&resolution=1D`
  + index via `instrument_name=deribit_price_index:btc_usd`

## Basis formula (all venues)

```
basis_usd      = futures_price − index_price
basis_pct_term = basis_usd / index_price
basis_pct_ann  = basis_pct_term × (365 / days_to_expiry)
```

All annualized values stored as decimals (0.05 = 5% p.a.).

## MVP refresh cadence

- Derivatives snapshots: every 5 minutes (APScheduler in `main.py`)
- Lending/yield snapshots: every 15 minutes
- Protocol risk params: every 15–60 minutes
- Page load: `POST /api/admin/refresh` fires automatically on first visit
  to guarantee fresh data regardless of background job timing

## Design principles

- Preserve source traceability (`raw_payload` on every snapshot)
- REST fallbacks for every critical data path — app works with zero API keys
- Keep services small and explicit; avoid premature abstraction
- Optimize for reliable desk usage, not flashy UX
- All Docker volumes mounted read-only for external codebases
