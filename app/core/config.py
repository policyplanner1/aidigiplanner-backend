from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    env: str = "development"
    log_level: str = "INFO"

    database_url: str = "mysql+aiomysql://root:@127.0.0.1:3306/aidigiplanner?charset=utf8mb4"
    # Separate database so the test suite never touches dev data. Tests build
    # their schema directly from SQLAlchemy metadata (see tests/conftest.py),
    # not via Alembic — keep this database's schema in sync by just letting
    # the test session fixture recreate it (it does, every run).
    test_database_url: str = (
        "mysql+aiomysql://root:@127.0.0.1:3306/aidigiplanner_test?charset=utf8mb4"
    )

    jwt_secret: str = "change-me-in-.env"
    jwt_algorithm: str = "HS256"
    access_token_ttl_minutes: int = 15
    refresh_token_ttl_days: int = 30

    # TTL of the reset session token issued by /verify-reset-otp, used by
    # the final /reset-password call — not the OTP itself, see otp_ttl_minutes.
    password_reset_ttl_minutes: int = 60

    # Forgot-password OTP: how long the emailed 6-digit code is valid, and
    # how many wrong guesses it tolerates before requiring a fresh one.
    otp_ttl_minutes: int = 10
    otp_max_attempts: int = 5

    # Register/resend-verification OTP (the 6-box "Verify your email" code).
    # Reuses otp_max_attempts above for its lockout — same shape as the
    # password-reset OTP, just a separate TTL.
    email_verification_otp_ttl_minutes: int = 15

    rate_limit_per_ip_per_minute: int = 20
    rate_limit_per_email_per_minute: int = 5

    # Empty smtp_host means "not configured" — get_email_service() falls
    # back to ConsoleEmailService in that case (e.g. local dev with no
    # mailbox set up). Field names match the SMTP_* env vars exactly so
    # pydantic-settings binds them with no alias needed.
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_pass: str = ""
    smtp_use_tls: bool = True
    smtp_from_name: str = "AI Social Planner"

    # Where CreativeAsset bytes (generated images/videos) live. "local"
    # is the only backend implemented so far; get_storage_service() raises
    # NotImplementedError for anything else until an S3Storage lands.
    creative_storage_backend: str = "local"
    creative_storage_local_root: str = "./storage/creatives"

    # Broker for the creative-generation job queue (arq). The API process
    # enqueues onto this; a separate `uv run arq app.modules.creatives.worker.WorkerSettings`
    # process consumes it -- see app/modules/creatives/worker.py.
    redis_url: str = "redis://127.0.0.1:6379/0"


@lru_cache
def get_settings() -> Settings:
    return Settings()
