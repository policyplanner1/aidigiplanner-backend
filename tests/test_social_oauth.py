from datetime import UTC, datetime, timedelta
from dataclasses import replace
from urllib.parse import parse_qs, unquote, urlparse

import jwt
from httpx import AsyncClient

from app.core.config import Settings, get_settings
from app.models.enums import SocialPlatform
from app.models.social_account import SocialAccount
from app.modules.social_accounts.google import GoogleBusinessOAuthClient, GoogleYouTubeOAuthClient
from app.modules.social_accounts.meta import MetaSocialOAuthClient
from app.modules.social_accounts.oauth import Auth0SocialOAuthClient, _sub_from_id_token
from app.modules.social_accounts.tokens import decrypt_secret
from tests.factories import register_and_login
from tests.fakes import (
    FakeSocialOAuthClient,
    RecordingEmailService,
    default_connected_facebook,
    default_connected_instagram,
)


def test_auth0_authorize_url_uses_connection_and_callback() -> None:
    client = Auth0SocialOAuthClient(
        Settings(
            auth0_domain="dev-example.us.auth0.com",
            auth0_client_id="client-id",
            auth0_client_secret="client-secret",
            auth0_callback_url="http://localhost:8000/auth/callback",
            auth0_instagram_connection="facebook",
            auth0_instagram_connection_scope="instagram_basic,pages_show_list",
        )
    )
    url = client.build_authorize_url(platform=SocialPlatform.instagram, state="signed-state")
    parsed = urlparse(url)
    assert parsed.netloc == "dev-example.us.auth0.com"
    params = parse_qs(parsed.query)
    assert params["connection"] == ["facebook"]
    assert params["redirect_uri"] == ["http://localhost:8000/auth/callback"]
    assert params["state"] == ["signed-state"]
    assert params["connection_scope"] == ["instagram_basic,pages_show_list"]


def test_auth0_authorize_url_omits_connection_scope_when_only_invalid() -> None:
    client = Auth0SocialOAuthClient(
        Settings(
            auth0_domain="dev-example.us.auth0.com",
            auth0_client_id="client-id",
            auth0_client_secret="client-secret",
            auth0_callback_url="http://localhost:8000/auth/callback",
            auth0_instagram_connection="facebook",
            auth0_instagram_connection_scope="instagram_content_publish,publish_pages",
        )
    )
    url = client.build_authorize_url(platform=SocialPlatform.instagram, state="signed-state")
    params = parse_qs(urlparse(url).query)
    assert "connection_scope" not in params


def test_auth0_authorize_url_drops_invalid_meta_scopes() -> None:
    client = Auth0SocialOAuthClient(
        Settings(
            auth0_domain="dev-example.us.auth0.com",
            auth0_client_id="client-id",
            auth0_client_secret="client-secret",
            auth0_callback_url="http://localhost:8000/auth/callback",
            auth0_instagram_connection="facebook",
            auth0_instagram_connection_scope=(
                "instagram_basic,pages_show_list,instagram_content_publish,publish_pages"
            ),
        )
    )
    url = client.build_authorize_url(platform=SocialPlatform.instagram, state="signed-state")
    params = parse_qs(urlparse(url).query)
    assert params["connection_scope"] == ["instagram_basic,pages_show_list"]
    assert "instagram_content_publish" not in params["connection_scope"][0]
    assert "publish_pages" not in params["connection_scope"][0]


def test_google_youtube_authorize_url_requests_offline_access() -> None:
    client = GoogleYouTubeOAuthClient(
        Settings(
            google_client_id="google-client-id",
            google_client_secret="google-client-secret",
            google_redirect_uri="http://localhost:8000/api/social/youtube/callback",
        )
    )
    url = client.build_authorize_url(platform=SocialPlatform.youtube, state="signed-state")
    parsed = urlparse(url)
    assert parsed.netloc == "accounts.google.com"
    params = parse_qs(parsed.query)
    assert params["client_id"] == ["google-client-id"]
    assert params["redirect_uri"] == ["http://localhost:8000/api/social/youtube/callback"]
    assert params["access_type"] == ["offline"]
    assert params["prompt"] == ["select_account consent"]
    assert params["state"] == ["signed-state"]
    scopes = unquote(params["scope"][0])
    assert "youtube.upload" in scopes
    assert "youtube.readonly" in scopes


def test_google_business_authorize_url_requests_offline_access() -> None:
    client = GoogleBusinessOAuthClient(
        Settings(
            google_client_id="google-client-id",
            google_client_secret="google-client-secret",
            google_business_redirect_uri="http://localhost:8000/api/social/google/callback",
        )
    )
    url = client.build_authorize_url(platform=SocialPlatform.google, state="signed-state")
    parsed = urlparse(url)
    assert parsed.netloc == "accounts.google.com"
    params = parse_qs(parsed.query)
    assert params["client_id"] == ["google-client-id"]
    assert params["redirect_uri"] == ["http://localhost:8000/api/social/google/callback"]
    assert params["access_type"] == ["offline"]
    assert params["prompt"] == ["consent"]
    assert params["state"] == ["signed-state"]
    scopes = unquote(params["scope"][0])
    assert "business.manage" in scopes


def test_instagram_authorize_url_uses_instagram_login() -> None:
    client = MetaSocialOAuthClient(
        Settings(
            meta_app_id="meta-app-id",
            meta_app_secret="meta-app-secret",
            meta_redirect_uri="http://localhost:8000/api/social/instagram/callback",
            meta_oauth_scope="instagram_basic,pages_show_list,instagram_content_publish",
        )
    )
    url = client.build_authorize_url(platform=SocialPlatform.instagram, state="signed-state")
    parsed = urlparse(url)
    assert parsed.netloc == "www.instagram.com"
    params = parse_qs(parsed.query)
    assert params["client_id"] == ["meta-app-id"]
    assert params["redirect_uri"] == ["http://localhost:8000/api/social/instagram/callback"]
    assert params["state"] == ["signed-state"]
    assert params["enable_fb_login"] == ["0"]
    assert params["scope"] == ["instagram_business_basic"]
    assert "extras" not in params


def test_meta_client_builds_facebook_profile() -> None:
    client = MetaSocialOAuthClient(
        Settings(meta_app_id="meta-app-id", meta_app_secret="meta-app-secret")
    )
    profile = client._facebook_profile(
        {"id": "111", "name": "Brand Page", "access_token": "page-token"},
        auth0_user_id="meta|1",
        expires_at=None,
        fallback_token="user-token",
    )
    assert profile.platform is SocialPlatform.facebook
    assert profile.handle == "Brand Page"
    assert profile.external_account_id == "111"


def test_facebook_authorize_url_is_not_instagram_onboarding() -> None:
    client = MetaSocialOAuthClient(
        Settings(
            meta_app_id="meta-app-id",
            meta_app_secret="meta-app-secret",
            meta_redirect_uri="http://localhost:8000/api/social/instagram/callback",
            meta_facebook_redirect_uri="http://localhost:8000/api/social/facebook/callback",
        )
    )
    url = client.build_authorize_url(platform=SocialPlatform.facebook, state="signed-state")
    parsed = urlparse(url)
    params = parse_qs(parsed.query)
    assert params["redirect_uri"] == ["http://localhost:8000/api/social/facebook/callback"]
    assert "extras" not in params
    assert "instagram_basic" not in params.get("scope", [""])[0]
    assert "pages_show_list" in params["scope"][0]
    assert "pages_manage_posts" in params["scope"][0]


def test_facebook_authorize_url_includes_manage_posts_when_configured() -> None:
    client = MetaSocialOAuthClient(
        Settings(
            meta_app_id="meta-app-id",
            meta_app_secret="meta-app-secret",
            meta_redirect_uri="http://localhost:8000/api/social/instagram/callback",
            meta_facebook_redirect_uri="http://localhost:8000/api/social/facebook/callback",
            meta_facebook_oauth_scope=(
                "pages_show_list,pages_read_engagement,pages_manage_posts,business_management"
            ),
        )
    )
    url = client.build_authorize_url(platform=SocialPlatform.facebook, state="signed-state")
    assert "pages_manage_posts" in parse_qs(urlparse(url).query)["scope"][0]


def test_debug_token_granular_scopes_yield_page_ids() -> None:
    from app.modules.social_accounts.oauth import _page_ids_from_debug_token, _pages_from_payload

    ids = _page_ids_from_debug_token(
        {
            "data": {
                "granular_scopes": [
                    {"scope": "pages_show_list", "target_ids": ["111", "222"]},
                    {"scope": "instagram_basic", "target_ids": ["111"]},
                ]
            }
        }
    )
    assert ids == ["111", "222"]
    nested = _pages_from_payload(
        {
            "accounts": {
                "data": [
                    {
                        "id": "111",
                        "name": "Brand Page",
                        "access_token": "page-token",
                        "instagram_business_account": {"id": "ig1", "username": "brand"},
                    }
                ]
            }
        }
    )
    assert nested[0]["id"] == "111"
    assert nested[0]["instagram"]["username"] == "brand"
    assert _pages_from_payload({"id": "user-1", "name": "Aftab"}) == []
    from_me_with_accounts = _pages_from_payload(
        {
            "id": "user-1",
            "name": "Aftab",
            "accounts": {
                "data": [{"id": "111", "name": "Brand Page", "access_token": "page-token"}]
            },
        }
    )
    assert [row["id"] for row in from_me_with_accounts] == ["111"]


def test_facebook_page_missing_error_explains_declined_pages() -> None:
    from app.modules.social_accounts.meta import _facebook_page_missing_error
    from app.modules.social_accounts.oauth import FacebookPagesLookup

    error = _facebook_page_missing_error(
        FacebookPagesLookup(pages=[], declined=("pages_show_list",)),
        instagram_found=False,
    )
    assert "declined" in error.message.lower()
    assert "personal" in error.message.lower()
    selected = _facebook_page_missing_error(
        FacebookPagesLookup(pages=[], granted=("pages_show_list", "public_profile")),
        instagram_found=False,
    )
    assert "tick" in selected.message.lower()


def test_id_token_with_audience_claim_is_accepted() -> None:
    settings = Settings(
        auth0_domain="dev-example.us.auth0.com",
        auth0_client_id="client-id",
        auth0_client_secret="client-secret",
        auth0_callback_url="http://localhost:8000/auth/callback",
    )
    token = jwt.encode(
        {
            "sub": "facebook|123",
            "iss": "https://dev-example.us.auth0.com/",
            "aud": "client-id",
            "exp": datetime.now(UTC) + timedelta(minutes=5),
        },
        "secret-key-for-tests-only-not-used-at-runtime",
        algorithm="HS256",
    )
    assert _sub_from_id_token(token, settings) == "facebook|123"


async def _create_product(client: AsyncClient, admin: dict, name: str = "OAuth Product") -> str:
    resp = await client.post(
        f"/api/v1/companies/{admin['company_id']}/products",
        json={"name": name},
        headers=admin["headers"],
    )
    assert resp.status_code == 201
    return resp.json()["id"]  # type: ignore[no-any-return]


def _assert_connected_response(
    resp: object, handle: str = "@connected_brand", platform: str = "instagram"
) -> None:
    assert hasattr(resp, "status_code")
    status_code = resp.status_code  # type: ignore[attr-defined]
    if get_settings().frontend_url:
        assert status_code == 302
        location = resp.headers["location"]  # type: ignore[attr-defined]
        assert "status=connected" in location
        assert f"platform={platform}" in location
        decoded = unquote(location.replace("+", " "))
        assert handle.lstrip("@") in decoded or handle in decoded
    else:
        assert status_code == 200
        if platform == "youtube":
            label = "YouTube"
        elif platform == "google":
            label = "Google Business Profile"
        elif platform == "facebook":
            label = "Facebook"
        else:
            label = "Instagram"
        assert f"{label} Connected" in resp.text  # type: ignore[attr-defined]


def _assert_error_response(resp: object) -> None:
    status_code = resp.status_code  # type: ignore[attr-defined]
    if get_settings().frontend_url:
        assert status_code == 302
        location = resp.headers["location"]  # type: ignore[attr-defined]
        assert "status=error" in location
    else:
        assert status_code == 400
        assert "failed" in resp.text.lower()  # type: ignore[attr-defined]


async def test_start_instagram_oauth_returns_authorize_url(
    client: AsyncClient, email_service: RecordingEmailService
) -> None:
    admin = await register_and_login(client, email_service, "oauth-admin1@example.com")
    product_id = await _create_product(client, admin)

    resp = await client.post(
        f"/api/v1/products/{product_id}/social-accounts/oauth/instagram",
        json={},
        headers=admin["headers"],
    )
    assert resp.status_code == 200
    url = resp.json()["authorize_url"]
    parsed = urlparse(url)
    assert parsed.netloc == "auth0.test"
    params = parse_qs(parsed.query)
    assert params["connection"] == ["instagram"]
    assert params["state"]


async def test_outsider_cannot_start_oauth(
    client: AsyncClient, email_service: RecordingEmailService
) -> None:
    admin = await register_and_login(client, email_service, "oauth-admin2@example.com")
    outsider = await register_and_login(client, email_service, "oauth-outsider2@example.com")
    product_id = await _create_product(client, admin)

    resp = await client.post(
        f"/api/v1/products/{product_id}/social-accounts/oauth/instagram",
        json={},
        headers=outsider["headers"],
    )
    assert resp.status_code == 404


async def test_oauth_callback_saves_encrypted_token(
    client: AsyncClient,
    email_service: RecordingEmailService,
    oauth_client: FakeSocialOAuthClient,
) -> None:
    admin = await register_and_login(client, email_service, "oauth-admin3@example.com")
    product_id = await _create_product(client, admin)

    start = await client.post(
        f"/api/v1/products/{product_id}/social-accounts/oauth/instagram",
        json={},
        headers=admin["headers"],
    )
    state = parse_qs(urlparse(start.json()["authorize_url"]).query)["state"][0]

    callback = await client.get("/auth/callback", params={"code": "auth0-code", "state": state})
    _assert_connected_response(callback)
    assert oauth_client.codes == ["auth0-code"]

    listed = await client.get(
        f"/api/v1/products/{product_id}/social-accounts", headers=admin["headers"]
    )
    assert listed.status_code == 200
    body = listed.json()
    assert len(body) == 1
    assert body[0]["platform"] == "instagram"
    assert body[0]["handle"] == "@connected_brand"
    assert body[0]["connection_method"] == "oauth"
    assert body[0]["external_account_id"] == "17841400000000000"
    assert "access_token" not in body[0]
    assert "access_token_encrypted" not in body[0]

    session = client.db_session  # type: ignore[attr-defined]
    account = await session.get(SocialAccount, body[0]["id"])
    assert account is not None
    assert account.access_token_encrypted is not None
    assert decrypt_secret(account.access_token_encrypted) == "ig-access-token"


async def test_oauth_callback_denied_redirects_error(
    client: AsyncClient, email_service: RecordingEmailService
) -> None:
    admin = await register_and_login(client, email_service, "oauth-admin4@example.com")
    product_id = await _create_product(client, admin)

    start = await client.post(
        f"/api/v1/products/{product_id}/social-accounts/oauth/instagram",
        json={},
        headers=admin["headers"],
    )
    state = parse_qs(urlparse(start.json()["authorize_url"]).query)["state"][0]

    callback = await client.get(
        "/auth/callback",
        params={"error": "access_denied", "error_description": "user denied", "state": state},
    )
    _assert_error_response(callback)

    listed = await client.get(
        f"/api/v1/products/{product_id}/social-accounts", headers=admin["headers"]
    )
    assert listed.json() == []


async def test_oauth_callback_rejects_invalid_state(client: AsyncClient) -> None:
    callback = await client.get(
        "/auth/callback", params={"code": "auth0-code", "state": "not-a-jwt"}
    )
    _assert_error_response(callback)


async def test_oauth_reconnect_updates_existing_account(
    client: AsyncClient, email_service: RecordingEmailService
) -> None:
    admin = await register_and_login(client, email_service, "oauth-admin5@example.com")
    product_id = await _create_product(client, admin)

    first_start = await client.post(
        f"/api/v1/products/{product_id}/social-accounts/oauth/instagram",
        json={},
        headers=admin["headers"],
    )
    first_state = parse_qs(urlparse(first_start.json()["authorize_url"]).query)["state"][0]
    await client.get("/auth/callback", params={"code": "first", "state": first_state})

    second_start = await client.post(
        f"/api/v1/products/{product_id}/social-accounts/oauth/instagram",
        json={},
        headers=admin["headers"],
    )
    second_state = parse_qs(urlparse(second_start.json()["authorize_url"]).query)["state"][0]
    second = await client.get("/auth/callback", params={"code": "second", "state": second_state})
    _assert_connected_response(second)

    listed = await client.get(
        f"/api/v1/products/{product_id}/social-accounts", headers=admin["headers"]
    )
    assert len(listed.json()) == 1


async def test_oauth_not_configured(
    client: AsyncClient,
    email_service: RecordingEmailService,
    oauth_client: FakeSocialOAuthClient,
) -> None:
    oauth_client.configured = False
    admin = await register_and_login(client, email_service, "oauth-admin6@example.com")
    product_id = await _create_product(client, admin)

    resp = await client.post(
        f"/api/v1/products/{product_id}/social-accounts/oauth/instagram",
        json={},
        headers=admin["headers"],
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "oauth_not_configured"


async def test_oauth_unsupported_platform(
    client: AsyncClient, email_service: RecordingEmailService
) -> None:
    admin = await register_and_login(client, email_service, "oauth-admin7@example.com")
    product_id = await _create_product(client, admin)

    resp = await client.post(
        f"/api/v1/products/{product_id}/social-accounts/oauth/linkedin",
        json={},
        headers=admin["headers"],
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "oauth_platform_unsupported"


async def test_start_youtube_oauth_returns_google_authorize_url(
    client: AsyncClient, email_service: RecordingEmailService
) -> None:
    admin = await register_and_login(client, email_service, "oauth-yt-admin1@example.com")
    product_id = await _create_product(client, admin, "YouTube Product")

    resp = await client.post(
        f"/api/v1/products/{product_id}/social-accounts/oauth/youtube",
        json={},
        headers=admin["headers"],
    )
    assert resp.status_code == 200
    url = resp.json()["authorize_url"]
    parsed = urlparse(url)
    assert parsed.netloc == "accounts.google.com"
    assert parse_qs(parsed.query)["state"]


async def test_youtube_connect_get_alias_returns_authorize_url(
    client: AsyncClient, email_service: RecordingEmailService
) -> None:
    admin = await register_and_login(client, email_service, "oauth-yt-admin2@example.com")
    product_id = await _create_product(client, admin, "YouTube Alias Product")

    resp = await client.get(
        "/api/social/youtube/connect",
        params={"product_id": product_id},
        headers=admin["headers"],
    )
    assert resp.status_code == 200
    assert urlparse(resp.json()["authorize_url"]).netloc == "accounts.google.com"


async def test_youtube_callback_saves_encrypted_tokens(
    client: AsyncClient,
    email_service: RecordingEmailService,
    oauth_client: FakeSocialOAuthClient,
) -> None:
    admin = await register_and_login(client, email_service, "oauth-yt-admin3@example.com")
    product_id = await _create_product(client, admin, "YouTube Callback Product")

    start = await client.post(
        f"/api/v1/products/{product_id}/social-accounts/oauth/youtube",
        json={},
        headers=admin["headers"],
    )
    state = parse_qs(urlparse(start.json()["authorize_url"]).query)["state"][0]

    callback = await client.get(
        "/api/social/youtube/callback", params={"code": "google-code", "state": state}
    )
    _assert_connected_response(callback, handle="@brandchannel", platform="youtube")
    assert oauth_client.codes == ["google-code"]

    listed = await client.get(
        f"/api/v1/products/{product_id}/social-accounts", headers=admin["headers"]
    )
    assert listed.status_code == 200
    body = listed.json()
    assert len(body) == 1
    assert body[0]["platform"] == "youtube"
    assert body[0]["handle"] == "@brandchannel"
    assert body[0]["connection_method"] == "oauth"
    assert body[0]["external_account_id"] == "UC1234567890ABCDEFGHIJKL"
    assert "access_token" not in body[0]
    assert "refresh_token" not in body[0]
    assert "access_token_encrypted" not in body[0]

    session = client.db_session  # type: ignore[attr-defined]
    account = await session.get(SocialAccount, body[0]["id"])
    assert account is not None
    assert account.access_token_encrypted is not None
    assert account.refresh_token_encrypted is not None
    assert decrypt_secret(account.access_token_encrypted) == "yt-access-token"
    assert decrypt_secret(account.refresh_token_encrypted) == "yt-refresh-token"


async def test_start_google_business_oauth_returns_google_authorize_url(
    client: AsyncClient, email_service: RecordingEmailService
) -> None:
    admin = await register_and_login(client, email_service, "oauth-gbp-admin1@example.com")
    product_id = await _create_product(client, admin, "GBP Product")

    resp = await client.post(
        f"/api/v1/products/{product_id}/social-accounts/oauth/google",
        json={},
        headers=admin["headers"],
    )
    assert resp.status_code == 200
    url = resp.json()["authorize_url"]
    parsed = urlparse(url)
    assert parsed.netloc == "accounts.google.com"
    assert parse_qs(parsed.query)["state"]


async def test_google_business_connect_get_alias_returns_authorize_url(
    client: AsyncClient, email_service: RecordingEmailService
) -> None:
    admin = await register_and_login(client, email_service, "oauth-gbp-admin2@example.com")
    product_id = await _create_product(client, admin, "GBP Alias Product")

    resp = await client.get(
        "/api/social/google/connect",
        params={"product_id": product_id},
        headers=admin["headers"],
    )
    assert resp.status_code == 200
    assert urlparse(resp.json()["authorize_url"]).netloc == "accounts.google.com"


async def test_google_business_callback_saves_encrypted_tokens(
    client: AsyncClient,
    email_service: RecordingEmailService,
    oauth_client: FakeSocialOAuthClient,
) -> None:
    admin = await register_and_login(client, email_service, "oauth-gbp-admin3@example.com")
    product_id = await _create_product(client, admin, "GBP Callback Product")

    start = await client.post(
        f"/api/v1/products/{product_id}/social-accounts/oauth/google",
        json={},
        headers=admin["headers"],
    )
    state = parse_qs(urlparse(start.json()["authorize_url"]).query)["state"][0]

    callback = await client.get(
        "/api/social/google/callback", params={"code": "gbp-code", "state": state}
    )
    _assert_connected_response(callback, handle="Brand Store Pune", platform="google")
    assert oauth_client.codes == ["gbp-code"]

    listed = await client.get(
        f"/api/v1/products/{product_id}/social-accounts", headers=admin["headers"]
    )
    assert listed.status_code == 200
    body = listed.json()
    assert len(body) == 1
    assert body[0]["platform"] == "google"
    assert body[0]["handle"] == "Brand Store Pune"
    assert body[0]["connection_method"] == "oauth"
    assert body[0]["external_account_id"] == "9876543210"
    assert "access_token" not in body[0]
    assert "refresh_token" not in body[0]

    session = client.db_session  # type: ignore[attr-defined]
    account = await session.get(SocialAccount, body[0]["id"])
    assert account is not None
    assert account.access_token_encrypted is not None
    assert account.refresh_token_encrypted is not None
    assert decrypt_secret(account.access_token_encrypted) == "gbp-access-token"
    assert decrypt_secret(account.refresh_token_encrypted) == "gbp-refresh-token"


async def test_start_facebook_oauth_returns_authorize_url(
    client: AsyncClient, email_service: RecordingEmailService
) -> None:
    admin = await register_and_login(client, email_service, "oauth-fb-admin1@example.com")
    product_id = await _create_product(client, admin, "Facebook Product")

    resp = await client.post(
        f"/api/v1/products/{product_id}/social-accounts/oauth/facebook",
        json={},
        headers=admin["headers"],
    )
    assert resp.status_code == 200
    parsed = urlparse(resp.json()["authorize_url"])
    assert parsed.netloc == "www.facebook.com"
    assert parse_qs(parsed.query)["state"]


async def test_instagram_connect_alias_and_callback(
    client: AsyncClient,
    email_service: RecordingEmailService,
    oauth_client: FakeSocialOAuthClient,
) -> None:
    admin = await register_and_login(client, email_service, "oauth-ig-alias@example.com")
    product_id = await _create_product(client, admin, "Instagram Alias Product")

    resp = await client.get(
        "/api/social/instagram/connect",
        params={"product_id": product_id},
        headers=admin["headers"],
    )
    assert resp.status_code == 200
    state = parse_qs(urlparse(resp.json()["authorize_url"]).query)["state"][0]
    callback = await client.get(
        "/api/social/instagram/callback", params={"code": "meta-code", "state": state}
    )
    _assert_connected_response(callback)
    assert oauth_client.codes == ["meta-code"]


async def test_instagram_callback_also_saves_linked_facebook_page(
    client: AsyncClient,
    email_service: RecordingEmailService,
    oauth_client: FakeSocialOAuthClient,
) -> None:
    facebook = default_connected_facebook()
    oauth_client.profiles[SocialPlatform.instagram] = replace(
        default_connected_instagram(), extra_profiles=(facebook,)
    )
    admin = await register_and_login(client, email_service, "oauth-ig-extra@example.com")
    product_id = await _create_product(client, admin, "Instagram Extra Product")

    start = await client.post(
        f"/api/v1/products/{product_id}/social-accounts/oauth/instagram",
        json={},
        headers=admin["headers"],
    )
    state = parse_qs(urlparse(start.json()["authorize_url"]).query)["state"][0]
    callback = await client.get(
        "/api/social/instagram/callback", params={"code": "meta-both", "state": state}
    )
    _assert_connected_response(callback)

    listed = await client.get(
        f"/api/v1/products/{product_id}/social-accounts", headers=admin["headers"]
    )
    body = listed.json()
    platforms = {row["platform"]: row["handle"] for row in body}
    assert platforms["instagram"] == "@connected_brand"
    assert platforms["facebook"] == "Brand Page"


async def test_facebook_callback_saves_encrypted_token(
    client: AsyncClient,
    email_service: RecordingEmailService,
    oauth_client: FakeSocialOAuthClient,
) -> None:
    admin = await register_and_login(client, email_service, "oauth-fb-callback@example.com")
    product_id = await _create_product(client, admin, "Facebook Callback Product")

    start = await client.post(
        f"/api/v1/products/{product_id}/social-accounts/oauth/facebook",
        json={},
        headers=admin["headers"],
    )
    state = parse_qs(urlparse(start.json()["authorize_url"]).query)["state"][0]
    callback = await client.get(
        "/api/social/facebook/callback", params={"code": "fb-code", "state": state}
    )
    _assert_connected_response(callback, handle="Brand Page", platform="facebook")

    listed = await client.get(
        f"/api/v1/products/{product_id}/social-accounts", headers=admin["headers"]
    )
    body = listed.json()
    assert len(body) == 1
    assert body[0]["platform"] == "facebook"
    assert body[0]["handle"] == "Brand Page"
    session = client.db_session  # type: ignore[attr-defined]
    account = await session.get(SocialAccount, body[0]["id"])
    assert account is not None
    assert decrypt_secret(account.access_token_encrypted) == "fb-page-token"
