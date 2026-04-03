# Book Management & Optimization System

The Book system provides institutional lending/trading desk portfolio management:
Excel workbook import, position classification, collateral tracking, and an
optimization engine that compares desk positions against live DeFi market data.

## Data flow

```
Excel Upload (CreditDesk WACC Export)
        │
        ▼
  book_import.py — parse, classify, persist
        │
        ▼
  DB: books, book_positions, book_observed_collateral, book_collateral_allocations
        │
        ▼
  book_optimizer.py — compare against market_opportunities
        │
        ▼
  Optimization suggestions, DeFi comparisons, bilateral pricing, collateral efficiency, maturities
        │
        ▼
  Frontend: /book — upload, summary dashboard, 6 analysis tabs
```

## Workbook format

The system expects a CreditDesk WACC Export (`.xlsx`) with three sheets:

| Sheet | Purpose |
|-------|---------|
| `Asset_Params` | Asset prices (columns: Asset, Price Usd) |
| `Trades_Raw` | Full loan tape — every loan/borrow with counterparty, rates, dates |
| `Observed_Collateral` | Collateral pledged per counterparty |

### Column mapping

The importer maps CreditDesk column headers to internal field names via
`_TRADES_COL_MAP` and `_COLLATERAL_COL_MAP` dictionaries. Known variants
are handled (e.g. both "Rehypothecation" and "Rehypothecation Allowed").

## Position classification

Every position is auto-classified into one of:

| Category | Rule |
|----------|------|
| `DEFI_SUPPLY` | Counterparty is a DeFi protocol, direction = Loan_Out |
| `DEFI_BORROW` | Counterparty is a DeFi protocol, direction = Borrow_In |
| `NATIVE_STAKING` | Protocol name contains "staking" or "custody" + native asset |
| `BILATERAL_LOAN_OUT` | Non-DeFi counterparty, direction = Loan_Out |
| `BILATERAL_BORROW_IN` | Non-DeFi counterparty, direction = Borrow_In |
| `INTERNAL` | Internal/treasury counterparties |
| `OFF_PLATFORM` | Off-platform or custody positions |

Protocol extraction uses regex on counterparty names:
`"Protocol (Chain) - Credit Desk Type"` → extracts protocol name and chain.

## Optimization engine

`BookOptimizer` runs 8 analysis passes (A–H):

| Pass | Type | What it does |
|------|------|-------------|
| A | `DEFI_RATE_IMPROVEMENT` | DeFi supply positions earning below best market rate |
| B | `DEFI_BORROW_OPTIMIZATION` | DeFi borrows paying above cheapest market rate |
| C | `BILATERAL_PRICING_CHECK` | Bilateral loans priced below DeFi alternative |
| D | `STAKING_RATE_CHECK` | Staking positions below current market staking yield |
| E | `COLLATERAL_EFFICIENCY` | Counterparties with excess collateral that could be deployed |
| F | `MATURITY_ACTION` | Fixed-term positions expiring within 30 days |
| G | `CAPACITY_WARNING` | Positions approaching protocol capacity limits |
| H | `CONVERSION_OPPORTUNITY` | Asset conversions (e.g. ETH→swETH→Pendle) with better yield |

### Rate filtering

To avoid misleading comparisons from small or incentivized pools:

- **Minimum TVL**: $5M — pools below this are excluded from "best rate" lookups
- **NULL TVL**: Excluded — pools with unknown TVL are not used as benchmarks
- **>100% APY**: Non-Pendle pools above 100% are filtered as anomalous
- **Pendle**: Pendle pools are allowed above 100% (legitimate fixed-yield strategies)

## API endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/book/import` | POST | Upload Excel workbook (multipart) |
| `/api/book/{id}` | GET | Book metadata |
| `/api/book/{id}/positions` | GET | All positions with filters |
| `/api/book/{id}/defi` | GET | DeFi positions only |
| `/api/book/{id}/collateral` | GET | Collateral summary |
| `/api/book/{id}/counterparty/{cid}` | GET | Single counterparty detail |
| `/api/book/{id}/summary` | GET | Aggregated stats for dashboard |
| `/api/book/{id}/refresh-matching` | POST | Re-match DeFi positions to market data |
| `/api/book/{id}/analyze` | POST | Run full optimization (returns suggestions) |
| `/api/book/{id}/defi-vs-market` | GET | DeFi position vs market rate table |
| `/api/book/{id}/bilateral-pricing` | GET | Bilateral loans vs DeFi pricing |
| `/api/book/{id}/collateral-efficiency` | GET | Per-counterparty collateral efficiency |
| `/api/book/{id}/maturity-calendar` | GET | Fixed-term maturity schedule |

## Frontend (`/book`)

The Book page is a single-page client with drag-and-drop Excel upload and 6 tabs:

| Tab | Content |
|-----|---------|
| Summary | 10 stat cards, category/asset/counterparty charts |
| DeFi Positions | Table with rate vs market delta coloring |
| Bilateral Book | Table with pricing assessment badges |
| Collateral | Counterparty accordions with efficiency metrics |
| Optimization | Prioritized suggestion cards with expandable detail |
| Maturities | Sorted maturity calendar with status badges |

## DB tables (migration 009)

| Table | Purpose |
|-------|---------|
| `books` | Book metadata (id, name, upload timestamp) |
| `book_positions` | Individual positions with classification |
| `book_observed_collateral` | Collateral observations per counterparty |
| `book_collateral_allocations` | Pro-rata collateral-to-position allocations |

## Key files

| File | Purpose |
|------|---------|
| `apps/api/app/services/book_import.py` | Excel parsing, classification, persistence |
| `apps/api/app/services/book_optimizer.py` | Optimization engine (8 passes, rate comparison) |
| `apps/api/app/routers/book.py` | 13 API endpoints |
| `apps/api/app/models/book.py` | SQLAlchemy ORM models |
| `apps/api/alembic/versions/009_book_tables.py` | DB migration |
| `apps/web/src/app/book/BookClient.tsx` | Full interactive UI (~700 lines) |
| `apps/web/src/app/book/page.tsx` | Server component wrapper |

## Verification results (2026-04-03)

Tested against real CreditDesk WACC Export:

- **263 positions** imported (3 DeFi supply, 3 DeFi borrow, 6 staking, 56 bilateral loan out, 115 bilateral borrow in, 48 internal, 32 off-platform)
- **$1.11B** loans out, **$1.45B** borrows in
- **40 counterparties** with collateral data
- **23 optimization suggestions**, **$5.5M** estimated annual impact
- **86 fixed-term** maturity positions tracked
- Top suggestions: ETH→swETH Pendle conversion ($2M/yr), SOL→Jupiter rate improvement ($1.1M/yr), Aave ETH→Lido ($987K/yr)
