"""
Shared HTTP client for Yield OS protocol adapters.

Provides a configured :class:`httpx.AsyncClient` with:
  - 30-second default timeout
  - Connection pooling (max_connections=20)
  - Retry on transient errors (3 attempts, exponential backoff)
  - 429 Retry-After awareness
  - Redis-backed response caching (optional — degrades gracefully)

Usage::

    from app.connectors.http_client import http, get_json, get_with_cache

    data = await get_json("https://api.example.com/pools")
    data = await get_with_cache(
        "https://api.example.com/pools",
        cache_key="example:pools",
        ttl_seconds=300,
    )
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import structlog

from app.core.config import settings

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Client configuration
# ---------------------------------------------------------------------------

_TIMEOUT = 30.0
_MAX_CONNECTIONS = 20
_RETRY_ATTEMPTS = 3
_RETRY_BASE_DELAY = 1.0  # seconds; doubles each attempt (1, 2, 4)
_USER_AGENT = "YieldOS/0.1 (https://github.com/yield-os)"

# ---------------------------------------------------------------------------
# Lazy-initialised singleton client
# ---------------------------------------------------------------------------

_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    """Return the module-level async client, creating it on first use."""
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(_TIMEOUT),
            limits=httpx.Limits(
                max_connections=_MAX_CONNECTIONS,
                max_keepalive_connections=_MAX_CONNECTIONS,
            ),
            headers={"User-Agent": _USER_AGENT},
            follow_redirects=True,
        )
    return _client


# Alias for external use (e.g. ``from http_client import http``)
http = _get_client


async def close() -> None:
    """Explicitly close the shared client (call on app shutdown)."""
    global _client
    if _client and not _client.is_closed:
        await _client.aclose()
        _client = None


# ---------------------------------------------------------------------------
# Redis helpers (lazy, best-effort)
# ---------------------------------------------------------------------------

_redis: Any = None  # redis.asyncio.Redis | None
_redis_init_attempted: bool = False


async def _get_redis() -> Any:
    """Return a Redis connection, or None if unavailable."""
    global _redis, _redis_init_attempted
    if _redis_init_attempted:
        return _redis
    _redis_init_attempted = True
    try:
        import redis.asyncio as aioredis

        _redis = aioredis.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_connect_timeout=2.0,
        )
        await _redis.ping()
        log.debug("http_client_redis_connected", url=settings.redis_url)
    except Exception as exc:
        log.warning("http_client_redis_unavailable", error=str(exc))
        _redis = None
    return _redis


# ---------------------------------------------------------------------------
# Retry + 429 handling
# ---------------------------------------------------------------------------


async def _request_with_retry(
    method: str,
    url: str,
    *,
    params: dict | None = None,
    json_body: Any | None = None,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    """
    Execute an HTTP request with retries and 429-awareness.

    Retries up to ``_RETRY_ATTEMPTS`` times on:
      - Network errors (connection refused, DNS failure, etc.)
      - Timeout errors
      - 429 Too Many Requests (respects Retry-After header)
      - 5xx server errors

    Non-retryable errors (4xx other than 429) propagate immediately.
    """
    client = _get_client()
    last_exc: Exception | None = None

    for attempt in range(_RETRY_ATTEMPTS):
        try:
            log.debug(
                "http_request",
                method=method,
                url=url,
                attempt=attempt + 1,
            )
            resp = await client.request(
                method,
                url,
                params=params,
                json=json_body,
                headers=headers,
            )

            # 429 — respect Retry-After then retry
            if resp.status_code == 429:
                retry_after = _parse_retry_after(resp)
                log.warning(
                    "http_rate_limited",
                    url=url,
                    retry_after=retry_after,
                    attempt=attempt + 1,
                )
                await asyncio.sleep(retry_after)
                continue

            # 5xx — retry with backoff
            if resp.status_code >= 500:
                log.warning(
                    "http_server_error",
                    url=url,
                    status=resp.status_code,
                    attempt=attempt + 1,
                )
                await asyncio.sleep(_RETRY_BASE_DELAY * (2 ** attempt))
                continue

            # Success or non-retryable client error — return as-is
            return resp

        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            last_exc = exc
            delay = _RETRY_BASE_DELAY * (2 ** attempt)
            log.warning(
                "http_transient_error",
                url=url,
                error=str(exc),
                attempt=attempt + 1,
                retry_in=delay,
            )
            if attempt < _RETRY_ATTEMPTS - 1:
                await asyncio.sleep(delay)

    # All retries exhausted
    if last_exc is not None:
        raise last_exc
    raise httpx.NetworkError(f"All {_RETRY_ATTEMPTS} retries exhausted for {url}")


def _parse_retry_after(resp: httpx.Response) -> float:
    """Extract delay from a 429 response's Retry-After header."""
    header = resp.headers.get("retry-after", "")
    try:
        return max(float(header), 0.5)
    except (ValueError, TypeError):
        return _RETRY_BASE_DELAY * 2


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


async def get_json(
    url: str,
    params: dict | None = None,
    headers: dict[str, str] | None = None,
) -> dict:
    """GET a URL and return the parsed JSON body. Raises on HTTP errors."""
    resp = await _request_with_retry("GET", url, params=params, headers=headers)
    resp.raise_for_status()
    return resp.json()


async def post_json(
    url: str,
    data: Any | None = None,
    headers: dict[str, str] | None = None,
) -> dict:
    """POST JSON to a URL and return the parsed JSON body."""
    resp = await _request_with_retry(
        "POST", url, json_body=data, headers=headers,
    )
    resp.raise_for_status()
    return resp.json()


async def get_with_cache(
    url: str,
    cache_key: str,
    ttl_seconds: int = 300,
    params: dict | None = None,
    headers: dict[str, str] | None = None,
) -> dict:
    """GET with Redis caching.  Falls back to direct fetch if Redis is down.

    Cache keys are prefixed with ``yos:http:`` to namespace them.
    """
    full_key = f"yos:http:{cache_key}"

    # Try cache read
    r = await _get_redis()
    if r is not None:
        try:
            cached = await r.get(full_key)
            if cached is not None:
                log.debug("http_cache_hit", key=full_key)
                return json.loads(cached)
        except Exception as exc:
            log.debug("http_cache_read_error", key=full_key, error=str(exc))

    # Cache miss — fetch from origin
    result = await get_json(url, params=params, headers=headers)

    # Try cache write (fire-and-forget — don't block on Redis failure)
    if r is not None:
        try:
            await r.set(full_key, json.dumps(result), ex=ttl_seconds)
            log.debug("http_cache_set", key=full_key, ttl=ttl_seconds)
        except Exception as exc:
            log.debug("http_cache_write_error", key=full_key, error=str(exc))

    return result
