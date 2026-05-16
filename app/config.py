"""Settings via environment variables."""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    debug: bool = False
    allowed_origins: list[str] = ["http://localhost:3000", "http://localhost:5173"]

    # Signal detection
    signal_ror_lower_ci_threshold: float = 1.0
    signal_min_cases: int = 3


settings = Settings()
