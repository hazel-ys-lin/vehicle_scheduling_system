from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # `extra="ignore"` so Postgres compose variables (POSTGRES_USER, etc.) in
    # .env don't trip validation — they're consumed by the Postgres container,
    # not by the app.
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/vehicle_scheduling"


settings = Settings()
