from decimal import Decimal
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "Retail Inventory API"
    api_prefix: str = "/api/v1"

    # Either a full DATABASE_URL, or the individual DB_* parts (password-safe).
    database_url: str | None = None
    db_host: str = "localhost"
    db_port: int = 5432
    db_name: str = "postgres"
    db_user: str = "postgres"
    db_password: str = ""

    jwt_secret: str
    jwt_algorithm: str = "HS256"
    access_token_minutes: int = 15
    refresh_token_days: int = 7
    reset_token_minutes: int = 30
    # Dummy app has no email delivery, so /forgot-password returns the token directly.
    expose_reset_token: bool = True

    login_max_attempts: int = 5
    login_window_seconds: int = 300

    currency: str = "INR"
    tax_rate: Decimal = Decimal("0.18")
    cors_origins: list[str] = ["*"]

    seed_admin_email: str = "admin@retailhub.in"
    seed_admin_password: str = "change-me"

    @property
    def sqlalchemy_url(self) -> str | URL:
        if self.database_url:
            return self.database_url
        return URL.create(
            "postgresql+psycopg",
            username=self.db_user,
            password=self.db_password,
            host=self.db_host,
            port=self.db_port,
            database=self.db_name,
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
