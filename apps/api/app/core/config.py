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

    # CoinGecko: Pro key lifts rate limits and unlocks /simple/price bulk endpoint
    # Free tier (no key) works for low-volume dev use
    coingecko_api_key: str = ""
    coingecko_api_url: str = "https://pro-api.coingecko.com/api/v3"

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
