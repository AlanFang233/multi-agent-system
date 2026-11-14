from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    openai_api_key: str = ""
    openai_model: str = "gpt-5-mini"
    database_url: str = "postgresql+asyncpg://agent:local_dev_change_me@127.0.0.1:5432/multi_agent"
    redis_url: str = "redis://127.0.0.1:6379/0"
    research_agent_url: str = "http://127.0.0.1:8001"
    coding_agent_url: str = "http://127.0.0.1:8002"
    analysis_agent_url: str = "http://127.0.0.1:8003"
    openai_base_url: str | None = None


@lru_cache

def get_settings() -> Settings:
    return Settings()
