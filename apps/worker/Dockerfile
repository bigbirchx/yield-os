FROM python:3.12-slim

WORKDIR /app

# redis-cli is needed by the Docker healthcheck
RUN apt-get update && apt-get install -y --no-install-recommends redis-tools \
    && rm -rf /var/lib/apt/lists/*

# Re-use the API requirements (same dependencies)
COPY apps/api/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install monorepo packages
COPY packages/asset-registry /packages/asset-registry
RUN pip install --no-cache-dir /packages/asset-registry
COPY packages/opportunity-schema /packages/opportunity-schema
RUN pip install --no-cache-dir /packages/opportunity-schema

# Copy the API code (worker imports from app.*)
COPY apps/api/ /api-src/

# Copy worker code
COPY apps/worker/ /worker-src/

# PYTHONPATH: /api-src for `app.*`, /worker-src for `config`/`jobs`
ENV PYTHONPATH="/api-src:/worker-src"

CMD ["python", "/worker-src/main.py"]
