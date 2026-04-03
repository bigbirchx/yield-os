"""
Worker-specific configuration.

Job schedule intervals, execution limits, and Redis key prefixes.
All values are overridable via environment variables.
"""
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class WorkerSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # ── Schedule intervals (seconds) ────────────────────────────────────
    interval_funding_rates: int = 60          # 1 minute
    interval_lending_protocols: int = 300     # 5 minutes
    interval_derivatives: int = 300           # 5 minutes
    interval_staking_savings: int = 900       # 15 minutes
    interval_cex_earn: int = 900              # 15 minutes
    interval_defillama: int = 600             # 10 minutes
    interval_defillama_extended: int = 14400  # 4 hours
    interval_snapshot_rates: int = 300        # 5 minutes
    interval_prune_stale: int = 3600          # 1 hour
    interval_health_report: int = 300         # 5 minutes
    interval_legacy_velo: int = 300           # 5 minutes
    interval_legacy_internal: int = 300       # 5 minutes
    interval_legacy_coingecko: int = 900      # 15 minutes
    interval_legacy_borrow_rates: int = 900   # 15 minutes
    interval_token_universe: int = 86400      # 24 hours
    interval_refresh_prices: int = 300        # 5 minutes

    # ── Max execution times (seconds) ───────────────────────────────────
    timeout_funding_rates: int = 30
    timeout_lending_protocols: int = 120
    timeout_derivatives: int = 60
    timeout_staking_savings: int = 60
    timeout_cex_earn: int = 60
    timeout_defillama: int = 180
    timeout_defillama_extended: int = 300
    timeout_snapshot_rates: int = 60
    timeout_prune_stale: int = 30
    timeout_health_report: int = 15
    timeout_token_universe: int = 120
    timeout_refresh_prices: int = 60

    # ── Failure thresholds ──────────────────────────────────────────────
    # After this many consecutive failures, log at ERROR level
    failure_alert_threshold: int = 5

    # ── Stale opportunity pruning ───────────────────────────────────────
    prune_max_age_hours: int = 24

    # ── Redis key prefixes ──────────────────────────────────────────────
    redis_prefix: str = "yos:worker"
    redis_health_key: str = "yos:worker:health"
    redis_trigger_channel: str = "yos:worker:trigger"
    redis_jobstore_db: int = 1


worker_settings = WorkerSettings()
