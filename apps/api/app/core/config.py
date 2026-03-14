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

    # Direct protocol connector settings
    # Aave: uses The Graph subgraph (free API key from https://thegraph.com)
    aave_subgraph_key: str = ""
    aave_subgraph_url: str = (
        "https://gateway-arbitrum.network.thegraph.com/api/{key}/subgraphs/id/"
        "JCNWRypm7FYwV8fx5HhzZPSFaMxgkPuw4TnWm89byKeU"
    )

    # Morpho Blue: public GraphQL API, no key required
    morpho_api_url: str = "https://blue-api.morpho.org/graphql"

    # Kamino: public REST API, no key required
    kamino_api_url: str = "https://api.kamino.finance"

    # CoinGecko: Pro key lifts rate limits and unlocks /simple/price bulk endpoint
    # Free tier (no key) works for low-volume dev use
    coingecko_api_key: str = ""
    coingecko_api_url: str = "https://pro-api.coingecko.com/api/v3"

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
