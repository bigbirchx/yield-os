FROM python:3.12-slim

WORKDIR /app

# Build context is the repo root so we can reference packages/
COPY apps/api/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install monorepo packages
COPY packages/asset-registry /packages/asset-registry
RUN pip install --no-cache-dir /packages/asset-registry
COPY packages/opportunity-schema /packages/opportunity-schema
RUN pip install --no-cache-dir /packages/opportunity-schema
COPY packages/route-optimizer /packages/route-optimizer
RUN pip install --no-cache-dir /packages/route-optimizer
COPY packages/portfolio /packages/portfolio
RUN pip install --no-cache-dir /packages/portfolio

COPY apps/api/ .

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
