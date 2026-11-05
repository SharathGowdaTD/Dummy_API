from decimal import Decimal
from functools import lru_cache
from typing import Literal

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

    # How callers prove who they are. One codebase, one mode per deployment,
    # so each URL shows a different kind of login on the same endpoints:
    #   bearer   POST /auth/login → Authorization: Bearer <jwt>   (default)
    #   basic    Authorization: Basic base64(email:password), every call
    #   api_key  X-API-Key header, ?api_key= or the retail_key cookie
    #   oauth2   client credentials at POST /oauth/token → Bearer <token>
    # The OpenAPI spec declares only the active mode (app/auth_modes.py).
    # A path prefix picks another mode per request: /basic, /apikey, /oauth2.
    auth_mode: Literal["bearer", "basic", "api_key", "oauth2"] = "oauth2"
    client_token_minutes: int = 15

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
