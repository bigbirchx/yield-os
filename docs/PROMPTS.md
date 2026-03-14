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
