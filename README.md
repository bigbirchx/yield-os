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
      core/     config, database session
      models/   SQLAlchemy ORM models
      routers/  HTTP route handlers
      services/ business logic (future)
      repositories/ data access (future)
    alembic/    migration scripts
    tests/
  web/          Next.js frontend
    src/app/    App Router pages and layouts
    src/lib/    API client helpers
  worker/       ingestion and analytics jobs (future)
db/
  migrations/   raw SQL (reference only)
docs/           project brief, architecture, prompts
```

## Prerequisites

- Docker and Docker Compose v2
- `.env` file in repo root (copy from `.env.example`)

## Quick start

```bash
# 1. copy env file
cp .env.example .env
# edit .env and fill in VELO_API_KEY and DEFILLAMA_API_KEY

# 2. start all services
docker compose up --build

# 3. run database migrations (first time, or after schema changes)
docker compose exec api alembic upgrade head
```

Services after startup:

| Service    | URL                        |
|------------|----------------------------|
| API docs   | http://localhost:8000/docs |
| Health     | http://localhost:8000/api/health |
| Frontend   | http://localhost:3000      |

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

### Run migrations locally

```bash
cd apps/api
alembic upgrade head
```

### Create a new migration

```bash
cd apps/api
alembic revision --autogenerate -m "describe change"
```

### Backend tests

```bash
cd apps/api
pytest
```

### Frontend

```bash
cd apps/web
cp .env.local.example .env.local
npm install
npm run dev
```

## Environment variables

See `.env.example` for the full list. Key variables:

| Variable            | Description                         |
|---------------------|-------------------------------------|
| `POSTGRES_*`        | Database connection settings        |
| `REDIS_URL`         | Redis connection string             |
| `VELO_API_KEY`      | Velo derivatives data API key       |
| `DEFILLAMA_API_KEY` | DeFiLlama yield/lending API key     |
| `LOG_LEVEL`         | API log level (default: INFO)       |
