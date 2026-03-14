# Architecture

## Apps
- apps/api -> FastAPI backend
- apps/web -> Next.js frontend
- apps/worker -> ingestion jobs and analytics jobs

## Database
- Postgres for normalized storage
- Raw JSON payloads preserved in snapshot tables
- Alembic for schema migrations

## Main data flow
1. Connectors pull source data
2. Source payloads are normalized into snapshot/history tables
3. Analytics jobs compute derived metrics and explanation scores
4. API serves normalized and derived views
5. Frontend renders cards, tables, charts, and explanation panels

## Core design choice
Model the ecosystem as:
- assets
- venues/protocols
- transforms between assets
- snapshot tables for market state
- derived route and quote outputs

## MVP refresh cadence
- derivatives snapshots: every 5 minutes
- lending/yield snapshots: every 5 to 15 minutes
- protocol risk params: every 15 to 60 minutes
- event overlays and aggregates: daily

## Design principles
- preserve source traceability
- keep services small and explicit
- avoid premature abstraction
- optimize for reliable desk usage, not flashy UX
