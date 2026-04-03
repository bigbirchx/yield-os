from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "development"
    api_port: int = 8000
    log_level: str = "INFO"

    postgres_user: str = "postgres"
    postgres_password: str = "postgres"
    postgres_db: str = "yield_cockpit"
    postgres_host: str = "localhost"
    postgres_port: int = 5432

    redis_url: str = "redis://localhost:6379/0"

    velo_api_key: str = ""
    defillama_api_key: str = ""

    # Aave: official GraphQL API — no key required, public endpoint
    # Source: https://aave.com/docs/aave-v3/getting-started/graphql
    aave_api_url: str = "https://api.v3.aave.com/graphql"
    # Comma-separated chain IDs to ingest (1=Eth, 42161=Arbitrum, 8453=Base, 10=Optimism)
    aave_chain_ids: str = "1,42161,8453,10"

    # Morpho Blue: public GraphQL API, no key required
    morpho_api_url: str = "https://blue-api.morpho.org/graphql"

    # Kamino: public REST API, no key required
    kamino_api_url: str = "https://api.kamino.finance"

    # DeFiLlama yields pools endpoint — used by Kamino liquidity and Jupiter adapters
    defillama_yields_url: str = "https://yields.llama.fi/pools"

    # CoinGecko: Pro key lifts rate limits and unlocks /simple/price bulk endpoint
    # Free tier (no key) works for low-volume dev use
    coingecko_api_key: str = ""
    # Informational only — the client auto-selects the correct base URL
    # based on key prefix: CG-xxxx = Demo (api.coingecko.com),
    # other = Pro (pro-api.coingecko.com), empty = free public.
    coingecko_api_url: str = "https://api.coingecko.com/api/v3"

    # Bullish exchange — credentials from .env
    # Public/private key pair from Bullish institutional portal
    bullish_public_key: str = ""
    bullish_private_key: str = ""
    bullish_api_hostname: str = ""
    bullish_options_mm_account_id: str = ""

    # Coinglass — secondary/cross-check source for funding rates
    # Free-tier basic endpoint works without a key; key required for advanced data
    coinglass_api_key: str = ""
    coinglass_api_url: str = "https://open-api.coinglass.com/public/v2"

    # Amberdata derivatives key — required for CME futures basis data only
    # Leave empty to skip CME venue (Deribit/Binance/OKX/Bybit work without it)
    amberdata_derivs_key: str = ""

    # Binance API credentials — for Binance Simple Earn signed endpoints
    # Leave empty to use DeFiLlama fallback for earn rates
    binance_api_key: str = ""
    binance_api_secret: str = ""

    # Compound V3 — Messari DeFi subgraphs per chain (no key required for hosted service)
    # Override with The Graph decentralized network URLs + API key for production
    compound_v3_ethereum_url: str = "https://api.thegraph.com/subgraphs/name/messari/compound-v3-ethereum"
    compound_v3_arbitrum_url: str = "https://api.thegraph.com/subgraphs/name/messari/compound-v3-arbitrum"
    compound_v3_base_url: str = "https://api.thegraph.com/subgraphs/name/messari/compound-v3-base"
    compound_v3_polygon_url: str = "https://api.thegraph.com/subgraphs/name/messari/compound-v3-polygon"
    compound_v3_optimism_url: str = "https://api.thegraph.com/subgraphs/name/messari/compound-v3-optimism"

    # Pendle Finance — public REST API, no key required
    pendle_api_url: str = "https://api-v2.pendle.finance/core"

    # Euler V2 — official hosted API
    euler_v2_url: str = "https://api.euler.finance/graphql"

    # SparkLend — subgraph (Aave V3 fork, Ethereum only)
    spark_url: str = "https://api.thegraph.com/subgraphs/name/marsfoundation/sparklend-mainnet"

    # Sky/Maker savings rates — for sDAI DSR and sUSDS SSR
    # DeFiLlama yields API provides these in a normalized format
    sky_savings_url: str = "https://yields.llama.fi/pools"

    # JustLend — Tron's dominant lending protocol, public REST API, no key required
    # Official docs: https://api2.justlend.link
    justlend_api_url: str = "https://api2.justlend.link"

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def database_url_sync(self) -> str:
        return (
            f"postgresql+psycopg2://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


settings = Settings()
