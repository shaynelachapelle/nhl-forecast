"""Settings read from environment variables, or from a local .env file."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql://postgres:postgres@127.0.0.1:54322/postgres"

    nhl_web_base: str = "https://api-web.nhle.com"
    nhl_stats_base: str = "https://api.nhle.com/stats/rest"
    
    user_agent: str = "nhl-forecast (+https://github.com/shaynelachapelle/nhl-forecast)"


@lru_cache
def get_settings() -> Settings:
    return Settings()
