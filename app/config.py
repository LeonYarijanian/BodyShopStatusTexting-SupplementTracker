"""Server-level settings read from .env (Section 13)."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    APP_SECRET_KEY: str = ""
    DATABASE_URL: str = "sqlite:///./bodyshop.db"
    ALLOW_LIVE_SMS: bool = False
    SCHEDULER_ENABLED: bool = True
    LOG_LEVEL: str = "INFO"
    TWILIO_ACCOUNT_SID: str = ""
    TWILIO_AUTH_TOKEN: str = ""
    PUBLIC_BASE_URL: str = ""

    def secret_key_problem(self) -> str | None:
        if not self.APP_SECRET_KEY:
            return "APP_SECRET_KEY is missing. Set it in .env (64 random hex characters)."
        if len(self.APP_SECRET_KEY) < 32:
            return "APP_SECRET_KEY is shorter than 32 characters. Set it in .env (64 random hex characters)."
        return None

    @property
    def cookies_secure(self) -> bool:
        return self.PUBLIC_BASE_URL.startswith("https://")


@lru_cache
def get_settings() -> Settings:
    return Settings()
