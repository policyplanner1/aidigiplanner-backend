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

    # SPA origin. Used for CORS and for the post-OAuth redirect after a
    # social account is connected (or the attempt fails).
    frontend_url: str = ""

    # Fallback OAuth broker for Instagram when META_APP_ID is not set
    # (Auth0 → Meta → /auth/callback). Direct Meta login is preferred.
    auth0_domain: str = ""
    auth0_client_id: str = ""
    auth0_client_secret: str = ""
    auth0_callback_url: str = "http://localhost:8000/auth/callback"
    # Auth0 social connection name. Instagram Graph usually needs the
    # Facebook connection (`facebook`); a native `instagram` connection
    # works if that's what is enabled in the Auth0 tenant.
    auth0_instagram_connection: str = "facebook"
    # Optional extra IdP scopes forwarded to Meta. Leave empty to use
    # whatever is already configured on the Auth0 connection.
    auth0_instagram_connection_scope: str = ""
    # Direct Meta Facebook Login for Instagram / Facebook Page connect.
    # When META_APP_ID and META_APP_SECRET are set, this path is used and
    # Auth0 Management API is not required.
    meta_app_id: str = ""
    meta_app_secret: str = ""
    # Instagram Login credentials from Use cases → Instagram → API setup
    # with Instagram login. These are NOT App settings → Basic. Using the
    # Facebook App ID on instagram.com/oauth/authorize returns
    # "Invalid platform app".
    instagram_app_id: str = ""
    instagram_app_secret: str = ""
    # Instagram Login Valid OAuth Redirect URI. Must be HTTPS and must match
    # Meta → Use cases → Instagram → API setup with Instagram login.
    # Do not use the webhook URL here — that is a different Meta field.
    meta_redirect_uri: str = "http://localhost:8000/api/social/instagram/callback"
    meta_facebook_redirect_uri: str = "http://localhost:8000/api/social/facebook/callback"
    meta_oauth_scope: str = "instagram_business_basic"
    meta_facebook_oauth_scope: str = (
        "pages_show_list,pages_read_engagement,pages_manage_posts,business_management"
    )
    # Optional Facebook Login for Business configuration id. When set, Meta
    # shows the Page picker from that configuration instead of classic Login.
    meta_login_config_id: str = ""
    # Shared secret Meta sends as hub.verify_token when subscribing the
    # Instagram webhook at /api/webhooks/instagram.
    meta_webhook_verify_token: str = "aidigiplanner-ig-verify"

    # Google OAuth client used to connect YouTube channels. Empty client_id
    # disables the YouTube flow.
    google_client_id: str = ""
    google_client_secret: str = ""
    google_redirect_uri: str = "http://localhost:8000/api/social/youtube/callback"
    # Separate redirect so YouTube and Business Profile do not share a callback.
    google_business_redirect_uri: str = "http://localhost:8000/api/social/google/callback"


@lru_cache
def get_settings() -> Settings:
    return Settings()
