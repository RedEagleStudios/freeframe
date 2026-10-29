"""Sign in with Discord: the public flow, the admin settings, and secret storage.

DB, Redis and Discord are all mocked: users come from patched lookups, Redis is
a dict, and the Discord HTTP calls are patched at the service boundary.
"""
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlparse

import pytest

from apps.api.config import settings
from apps.api.models.instance_auth_settings import InstanceAuthSettings
from apps.api.models.user import User, UserStatus
from apps.api.services import discord_oauth
from apps.api.services.auth_service import decode_token
from apps.api.services.instance_secrets import decrypt_secret, encrypt_secret

FRONTEND = "http://localhost:3000"
SERVER = "123456789012345678"
ROLE_OK = "222222222222222222"
ROLE_OTHER = "333333333333333333"
NOT_PASSED = object()


class FakeRedis:
    def __init__(self):
        self.store: dict[str, str] = {}
        self.ttls: dict[str, int] = {}

    def setex(self, key, ttl, value):
        self.store[key] = value
        self.ttls[key] = ttl

    def getdel(self, key):
        self.ttls.pop(key, None)
        return self.store.pop(key, None)


@pytest.fixture
def fake_redis():
    r = FakeRedis()
    with patch("apps.api.services.discord_oauth.get_redis", return_value=r), \
         patch("apps.api.middleware.rate_limit.check_rate_limit", return_value=(True, 0)):
        yield r


@pytest.fixture
def no_env(monkeypatch):
    for name in ("discord_client_id", "discord_client_secret", "discord_server_id",
                 "discord_server_roles", "discord_oauth_redirect_uri"):
        monkeypatch.setattr(settings, name, None)
    monkeypatch.setattr(settings, "discord_login_enabled", False)
    monkeypatch.setattr(settings, "frontend_url", FRONTEND)


def _row(enabled=True, client_id="1100000000000000000", secret="shh", server_id=None, roles=None):
    return InstanceAuthSettings(
        id=uuid.uuid4(),
        discord_enabled=enabled,
        discord_client_id=client_id,
        discord_client_secret_encrypted=encrypt_secret(secret) if secret else None,
        discord_server_id=server_id,
        discord_allowed_role_ids=roles,
    )


def _use_row(row):
    return patch("apps.api.services.discord_oauth.get_auth_settings_row", return_value=row)


@pytest.fixture
def configured(no_env):
    row = _row()
    with _use_row(row):
        yield row


@pytest.fixture
def server_configured(no_env):
    row = _row(server_id=SERVER)
    with _use_row(row):
        yield row


def _user(status=UserStatus.active, email_verified=True, invite_token=None, invite_expires=None):
    u = MagicMock()
    u.id = uuid.uuid4()
    u.email = "person@example.com"
    u.name = "Admin Given Name"
    u.status = status
    u.email_verified = email_verified
    u.invite_token = invite_token
    u.invite_token_expires_at = invite_expires
    u.token_version = 3
    u.deleted_at = None
    u.is_superadmin = False
    return u


def _profile(**over):
    p = {"id": "999", "username": "pat", "global_name": "Pat Global",
         "email": "person@example.com", "verified": True}
    p.update(over)
    return p


def _start(client, from_path=None):
    params = {"from": from_path} if from_path is not None else {}
    resp = client.get("/auth/discord/start", params=params, follow_redirects=False)
    assert resp.status_code == 302
    state = parse_qs(urlparse(resp.headers["location"]).query)["state"][0]
    return resp, state


def _callback(client, state, user, profile=None, member=NOT_PASSED, owner=None):
    profile = profile if profile is not None else _profile()
    member_patch = (
        patch("apps.api.services.discord_oauth.fetch_guild_member")
        if member is NOT_PASSED
        else patch("apps.api.services.discord_oauth.fetch_guild_member", return_value=member)
    )
    with patch("apps.api.services.discord_oauth.exchange_code", return_value="d-access") as ex, \
         patch("apps.api.services.discord_oauth.fetch_profile", return_value=profile), \
         member_patch as gm, \
         patch("apps.api.routers.auth_discord.get_user_by_email", return_value=user), \
         patch("apps.api.routers.auth_discord.get_email_owner", return_value=owner):
        resp = client.get(
            "/auth/discord/callback", params={"code": "c0de", "state": state}, follow_redirects=False,
        )
    return resp, ex, gm


def _ticket_from(resp) -> str:
    assert resp.status_code == 302
    loc = resp.headers["location"]
    assert loc.startswith(f"{FRONTEND}/auth/discord/callback?ticket="), loc
    return parse_qs(urlparse(loc).query)["ticket"][0]


def _error_of(resp) -> str:
    assert resp.status_code == 302
    loc = resp.headers["location"]
    assert loc.startswith(f"{FRONTEND}/login?error="), loc
    return parse_qs(urlparse(loc).query)["error"][0]


# ── providers / start ─────────────────────────────────────────────────────────

def test_providers_reports_discord_only_when_usable(client, no_env, fake_redis):
    with _use_row(None):
        assert client.get("/auth/providers").json()["discord"] is False
    with _use_row(_row(enabled=False)):
        assert client.get("/auth/providers").json()["discord"] is False
    with _use_row(_row(secret=None)):
        assert client.get("/auth/providers").json()["discord"] is False
    with _use_row(_row()):
        assert client.get("/auth/providers").json()["discord"] is True


def test_providers_env_fallback(client, no_env, fake_redis, monkeypatch):
    monkeypatch.setattr(settings, "discord_client_id", "env-id")
    monkeypatch.setattr(settings, "discord_client_secret", "env-secret")
    monkeypatch.setattr(settings, "discord_login_enabled", True)
    with _use_row(None):
        assert client.get("/auth/providers").json()["discord"] is True


def test_env_server_and_roles_fallback(no_env, monkeypatch):
    monkeypatch.setattr(settings, "discord_server_id", SERVER)
    monkeypatch.setattr(settings, "discord_server_roles", f"{ROLE_OK}, {ROLE_OTHER}")
    config = discord_oauth.resolve_discord_config(_row())
    assert config.server_id == SERVER
    assert config.allowed_role_ids == [ROLE_OK, ROLE_OTHER]


def test_start_not_configured_redirects_to_login(client, no_env, fake_redis):
    with _use_row(None):
        resp = client.get("/auth/discord/start", follow_redirects=False)
    assert _error_of(resp) == "discord_not_configured"
    assert fake_redis.store == {}


def test_start_redirects_to_discord_with_state_cookie(client, configured, fake_redis):
    resp, state = _start(client, "/projects/abc?tab=comments")
    url = urlparse(resp.headers["location"])
    q = {k: v[0] for k, v in parse_qs(url.query).items()}
    assert f"{url.scheme}://{url.netloc}{url.path}" == discord_oauth.DISCORD_AUTHORIZE_URL
    assert q["response_type"] == "code"
    assert q["scope"] == "identify email"
    assert q["prompt"] == "none"
    assert q["client_id"] == "1100000000000000000"
    assert q["redirect_uri"] == f"{FRONTEND}/api/auth/discord/callback"
    assert discord_oauth.consume_flow(state) == {"from": "/projects/abc?tab=comments"}

    cookie = resp.headers["set-cookie"]
    assert f"ff_discord_state={state}" in cookie
    assert "HttpOnly" in cookie and "samesite=lax" in cookie.lower() and "Path=/" in cookie
    assert "Secure" not in cookie  # http deployment


def test_scope_includes_guild_members_only_with_server(client, server_configured, fake_redis):
    resp, _ = _start(client)
    q = parse_qs(urlparse(resp.headers["location"]).query)
    assert q["scope"] == ["identify email guilds.members.read"]


def test_state_cookie_secure_on_https(client, configured, fake_redis, monkeypatch):
    monkeypatch.setattr(settings, "frontend_url", "https://ff.example.com/freeframe")
    resp, _ = _start(client)
    assert "Secure" in resp.headers["set-cookie"]
    q = parse_qs(urlparse(resp.headers["location"]).query)
    # The API lives at the origin root even on a sub-path install.
    assert q["redirect_uri"] == ["https://ff.example.com/api/auth/discord/callback"]


def test_redirect_uri_env_override(no_env, monkeypatch):
    monkeypatch.setattr(settings, "discord_oauth_redirect_uri", "http://localhost:8000/auth/discord/callback")
    assert discord_oauth.discord_redirect_uri() == "http://localhost:8000/auth/discord/callback"


@pytest.mark.parametrize("value, expected", [
    ("/projects/x?y=1#z", "/projects/x?y=1#z"),
    ("//evil.com", "/"),
    ("https://evil.com", "/"),
    ("/\\evil", "/"),
    ("javascript:alert(1)", "/"),
    ("/ok\npath", "/"),
    ("", "/"),
    (None, "/"),
])
def test_safe_internal_path(value, expected):
    assert discord_oauth.safe_internal_path(value) == expected


def test_from_is_sanitized_through_the_whole_flow(client, configured, fake_redis):
    _, state = _start(client, "//evil.com")
    ticket = _ticket_from(_callback(client, state, _user())[0])
    with patch("apps.api.routers.auth_discord.get_user_by_id", return_value=_user()):
        body = client.post("/auth/discord/exchange", json={"ticket": ticket}).json()
    assert body["redirect_to"] == "/"


# ── callback: existing accounts ───────────────────────────────────────────────

def test_active_user_gets_tokens_via_single_use_ticket(client, configured, fake_redis):
    user = _user()
    _, state = _start(client, "/projects/p1")
    resp, ex, gm = _callback(client, state, user)
    ticket = _ticket_from(resp)
    config_arg, code_arg = ex.call_args.args
    assert code_arg == "c0de" and config_arg.client_secret == "shh"
    gm.assert_not_called()  # no server configured
    set_cookie = resp.headers["set-cookie"]
    assert "ff_discord_state=" in set_cookie and ("Max-Age=0" in set_cookie or "expires=" in set_cookie.lower())
    assert "access_token" not in resp.headers["location"]
    assert fake_redis.ttls[f"{discord_oauth.TICKET_PREFIX}{ticket}"] == 60

    with patch("apps.api.routers.auth_discord.get_user_by_id", return_value=user):
        first = client.post("/auth/discord/exchange", json={"ticket": ticket})
        second = client.post("/auth/discord/exchange", json={"ticket": ticket})
    assert first.status_code == 200
    body = first.json()
    assert body["needs_password"] is False
    assert body["redirect_to"] == "/projects/p1"
    access = decode_token(body["access_token"])
    assert access["sub"] == str(user.id) and access["ver"] == 3
    assert decode_token(body["refresh_token"])["sub"] == str(user.id)

    assert second.status_code == 400
    assert second.json() == {"detail": "Invalid or expired sign-in ticket"}


def test_unknown_ticket_refused(client, fake_redis):
    resp = client.post("/auth/discord/exchange", json={"ticket": "nope"})
    assert resp.status_code == 400
    assert resp.json() == {"detail": "Invalid or expired sign-in ticket"}


def test_exchange_refuses_user_deactivated_after_callback(client, configured, fake_redis):
    _, state = _start(client)
    ticket = _ticket_from(_callback(client, state, _user())[0])
    with patch("apps.api.routers.auth_discord.get_user_by_id",
               return_value=_user(status=UserStatus.deactivated)):
        assert client.post("/auth/discord/exchange", json={"ticket": ticket}).status_code == 400


def test_unknown_email_without_server_refused_no_account_created(client, configured, fake_redis, mock_db):
    _, state = _start(client)
    resp, _, _ = _callback(client, state, None)
    assert _error_of(resp) == "discord_no_account"
    mock_db.add.assert_not_called()
    assert not any(k.startswith(discord_oauth.TICKET_PREFIX) for k in fake_redis.store)


def test_deactivated_user_refused(client, configured, fake_redis):
    _, state = _start(client)
    resp, _, _ = _callback(client, state, _user(status=UserStatus.deactivated))
    assert _error_of(resp) == "discord_account_disabled"


@pytest.mark.parametrize("profile", [
    _profile(verified=False),
    _profile(verified=None),
    {k: v for k, v in _profile().items() if k != "verified"},
    _profile(email=None),
])
def test_unverified_or_missing_discord_email_refused(client, server_configured, fake_redis, mock_db, profile):
    _, state = _start(client)
    resp, _, _ = _callback(client, state, None, profile=profile, member={"roles": []})
    assert _error_of(resp) == "discord_email_unverified"
    mock_db.add.assert_not_called()


def test_pending_invitee_is_activated(client, configured, fake_redis, mock_db):
    user = _user(
        status=UserStatus.pending_invite, email_verified=False, invite_token="tok",
        invite_expires=datetime.now(timezone.utc) + timedelta(days=2),
    )
    _, state = _start(client)
    _ticket_from(_callback(client, state, user)[0])
    assert user.status == UserStatus.active
    assert user.email_verified is True
    assert user.invite_token is None and user.invite_token_expires_at is None
    assert user.name == "Admin Given Name"
    mock_db.commit.assert_called()


def test_expired_invite_refused(client, configured, fake_redis):
    user = _user(
        status=UserStatus.pending_invite, email_verified=False, invite_token="tok",
        invite_expires=datetime.now(timezone.utc) - timedelta(minutes=1),
    )
    _, state = _start(client)
    resp, _, _ = _callback(client, state, user)
    assert _error_of(resp) == "discord_invite_expired"
    assert user.status == UserStatus.pending_invite
    assert user.invite_token == "tok"


# ── callback: state ───────────────────────────────────────────────────────────

def test_state_cookie_mismatch_refused(client, configured, fake_redis):
    _, state = _start(client)
    client.cookies.clear()
    client.cookies.set("ff_discord_state", "someone-elses-nonce")
    resp, ex, _ = _callback(client, state, _user())
    assert _error_of(resp) == "discord_failed"
    ex.assert_not_called()


def test_missing_state_cookie_refused(client, configured, fake_redis):
    _, state = _start(client)
    client.cookies.clear()
    resp, ex, _ = _callback(client, state, _user())
    assert _error_of(resp) == "discord_failed"
    ex.assert_not_called()


def test_replayed_state_refused(client, configured, fake_redis):
    _, state = _start(client)
    _ticket_from(_callback(client, state, _user())[0])
    client.cookies.set("ff_discord_state", state)  # an attacker replaying both
    second, ex, _ = _callback(client, state, _user())
    assert _error_of(second) == "discord_failed"
    ex.assert_not_called()


def test_discord_error_param_and_exchange_failure(client, configured, fake_redis):
    _, state = _start(client)
    resp = client.get("/auth/discord/callback", params={"error": "access_denied", "state": state},
                      follow_redirects=False)
    assert _error_of(resp) == "discord_failed"

    _, state = _start(client)
    with patch("apps.api.services.discord_oauth.exchange_code",
               side_effect=discord_oauth.DiscordOAuthError("invalid_grant")):
        resp = client.get("/auth/discord/callback", params={"code": "c", "state": state},
                          follow_redirects=False)
    assert _error_of(resp) == "discord_failed"


# ── callback: server gating and provisioning ──────────────────────────────────

def test_not_in_server_refused(client, server_configured, fake_redis, mock_db):
    _, state = _start(client)
    resp, _, gm = _callback(client, state, _user(), member=None)
    assert _error_of(resp) == "discord_not_in_server"
    gm.assert_called_once_with("d-access", SERVER)


def test_not_in_server_refused_even_for_existing_user_and_no_provision(client, server_configured, fake_redis, mock_db):
    _, state = _start(client)
    resp, _, _ = _callback(client, state, None, member=None)
    assert _error_of(resp) == "discord_not_in_server"
    mock_db.add.assert_not_called()


def test_missing_role_refused(client, no_env, fake_redis, mock_db):
    with _use_row(_row(server_id=SERVER, roles=[ROLE_OK])):
        _, state = _start(client)
        resp, _, _ = _callback(client, state, _user(), member={"roles": [ROLE_OTHER]})
    assert _error_of(resp) == "discord_missing_role"


def test_allowed_role_passes(client, no_env, fake_redis):
    with _use_row(_row(server_id=SERVER, roles=[ROLE_OK])):
        _, state = _start(client)
        resp, _, _ = _callback(client, state, _user(), member={"roles": [ROLE_OTHER, ROLE_OK]})
    _ticket_from(resp)


def test_member_without_account_is_provisioned(client, server_configured, fake_redis, mock_db):
    _, state = _start(client, "/projects")
    resp, _, _ = _callback(
        client, state, None,
        profile=_profile(email="New.Person@Example.com"),
        member={"nick": "Server Nick", "roles": []},
    )
    ticket = _ticket_from(resp)
    created = mock_db.add.call_args.args[0]
    assert isinstance(created, User)
    assert created.email == "new.person@example.com"
    assert created.name == "Server Nick"
    assert created.status == UserStatus.active
    assert created.email_verified is True
    assert created.is_superadmin is False
    assert created.password_hash is None
    mock_db.commit.assert_called()
    stored = discord_oauth.consume_ticket(ticket)
    assert stored == {"user_id": str(created.id), "from": "/projects"}


@pytest.mark.parametrize("profile, member, expected", [
    (_profile(), {"nick": None}, "Pat Global"),
    (_profile(global_name=None), {}, "pat"),
])
def test_provisioned_name_fallbacks(client, server_configured, fake_redis, mock_db, profile, member, expected):
    _, state = _start(client)
    _ticket_from(_callback(client, state, None, profile=profile, member=member)[0])
    assert mock_db.add.call_args.args[0].name == expected


def test_deleted_account_email_not_reprovisioned(client, server_configured, fake_redis, mock_db):
    owner = _user()
    owner.deleted_at = datetime.now(timezone.utc)
    _, state = _start(client)
    resp, _, _ = _callback(client, state, None, member={"roles": []}, owner=owner)
    assert _error_of(resp) == "discord_account_disabled"
    mock_db.add.assert_not_called()


# ── Discord HTTP ──────────────────────────────────────────────────────────────

def test_exchange_code_posts_secret(no_env):
    config = discord_oauth.DiscordConfig(True, "cid", "sec", None)
    fake = MagicMock(status_code=200)
    fake.json.return_value = {"access_token": "at"}
    with patch("apps.api.services.discord_oauth.httpx.post", return_value=fake) as post:
        assert discord_oauth.exchange_code(config, "code") == "at"
    assert post.call_args.args[0] == "https://discord.com/api/oauth2/token"
    data = post.call_args.kwargs["data"]
    assert data == {
        "grant_type": "authorization_code", "code": "code",
        "redirect_uri": f"{FRONTEND}/api/auth/discord/callback",
        "client_id": "cid", "client_secret": "sec",
    }
    assert post.call_args.kwargs["timeout"] is not None

    fake.status_code = 400
    with patch("apps.api.services.discord_oauth.httpx.post", return_value=fake):
        with pytest.raises(discord_oauth.DiscordOAuthError):
            discord_oauth.exchange_code(config, "code")


@pytest.mark.parametrize("status, expected", [(404, None), (403, None)])
def test_guild_member_not_member(status, expected):
    with patch("apps.api.services.discord_oauth.httpx.get", return_value=MagicMock(status_code=status)) as get:
        assert discord_oauth.fetch_guild_member("at", SERVER) is expected
    assert get.call_args.args[0] == f"https://discord.com/api/users/@me/guilds/{SERVER}/member"
    assert get.call_args.kwargs["headers"] == {"Authorization": "Bearer at"}


def test_guild_member_other_errors_raise():
    with patch("apps.api.services.discord_oauth.httpx.get", return_value=MagicMock(status_code=500)):
        with pytest.raises(discord_oauth.DiscordOAuthError):
            discord_oauth.fetch_guild_member("at", SERVER)


# ── admin settings ────────────────────────────────────────────────────────────

@pytest.fixture
def superadmin(test_user):
    test_user.is_superadmin = True
    return test_user


def test_admin_get_never_returns_secret(client, auth_headers, superadmin, no_env):
    with _use_row(_row(server_id=SERVER, roles=[ROLE_OK])):
        resp = client.get("/admin/auth/discord", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json() == {
        "enabled": True,
        "client_id": "1100000000000000000",
        "client_secret_set": True,
        "server_id": SERVER,
        "allowed_role_ids": [ROLE_OK],
        "redirect_uri": f"{FRONTEND}/api/auth/discord/callback",
        "configured_via": "database",
    }
    assert "shh" not in resp.text


def test_admin_get_env_and_none(client, auth_headers, superadmin, no_env, monkeypatch):
    with _use_row(None):
        assert client.get("/admin/auth/discord", headers=auth_headers).json()["configured_via"] == "none"
        monkeypatch.setattr(settings, "discord_client_id", "env-id")
        body = client.get("/admin/auth/discord", headers=auth_headers).json()
    assert body["configured_via"] == "environment"
    assert body["client_id"] == "env-id" and body["client_secret_set"] is False


def test_admin_put_omitted_secret_kept_empty_clears(client, auth_headers, superadmin, configured):
    stored = configured.discord_client_secret_encrypted
    resp = client.put("/admin/auth/discord", headers=auth_headers, json={"client_id": "42"})
    assert resp.status_code == 200
    assert configured.discord_client_secret_encrypted == stored
    assert resp.json()["client_id"] == "42" and resp.json()["client_secret_set"] is True

    resp = client.put("/admin/auth/discord", headers=auth_headers,
                      json={"enabled": False, "client_secret": ""})
    assert resp.status_code == 200
    assert configured.discord_client_secret_encrypted is None
    assert resp.json()["client_secret_set"] is False


def test_admin_put_encrypts_new_secret(client, auth_headers, superadmin, configured):
    resp = client.put("/admin/auth/discord", headers=auth_headers, json={"client_secret": "brand-new"})
    assert resp.status_code == 200
    assert "brand-new" not in configured.discord_client_secret_encrypted
    assert decrypt_secret(configured.discord_client_secret_encrypted) == "brand-new"
    assert "brand-new" not in resp.text


def test_admin_put_server_and_roles(client, auth_headers, superadmin, configured):
    resp = client.put("/admin/auth/discord", headers=auth_headers,
                      json={"server_id": f" {SERVER} ", "allowed_role_ids": [ROLE_OK, ROLE_OK, " "]})
    assert resp.status_code == 200
    assert configured.discord_server_id == SERVER
    assert configured.discord_allowed_role_ids == [ROLE_OK]
    assert resp.json()["allowed_role_ids"] == [ROLE_OK]


def test_admin_put_roles_without_server_422(client, auth_headers, superadmin, configured, mock_db):
    resp = client.put("/admin/auth/discord", headers=auth_headers, json={"allowed_role_ids": [ROLE_OK]})
    assert resp.status_code == 422
    mock_db.commit.assert_not_called()


@pytest.mark.parametrize("body", [{"server_id": "abc"}, {"allowed_role_ids": ["12x"]}])
def test_admin_put_rejects_non_snowflake(client, auth_headers, superadmin, configured, body):
    assert client.put("/admin/auth/discord", headers=auth_headers, json=body).status_code == 422


def test_admin_put_enable_without_credentials_422(client, auth_headers, superadmin, no_env, mock_db):
    with _use_row(None):
        resp = client.put("/admin/auth/discord", headers=auth_headers,
                          json={"enabled": True, "client_id": "42"})
    assert resp.status_code == 422
    mock_db.commit.assert_not_called()


def test_admin_put_clearing_secret_of_enabled_provider_422(client, auth_headers, superadmin, configured, mock_db):
    resp = client.put("/admin/auth/discord", headers=auth_headers, json={"client_secret": None})
    assert resp.status_code == 422
    mock_db.commit.assert_not_called()


def test_admin_put_creates_row(client, auth_headers, superadmin, no_env, mock_db):
    with _use_row(None):
        resp = client.put("/admin/auth/discord", headers=auth_headers,
                          json={"enabled": True, "client_id": "42", "client_secret": "sec"})
    assert resp.status_code == 200
    assert resp.json()["enabled"] is True and resp.json()["configured_via"] == "database"
    added = mock_db.add.call_args.args[0]
    assert isinstance(added, InstanceAuthSettings)
    assert decrypt_secret(added.discord_client_secret_encrypted) == "sec"


@pytest.mark.parametrize("method", ["get", "put"])
def test_admin_endpoints_need_superadmin(client, auth_headers, test_user, configured, method):
    resp = getattr(client, method)("/admin/auth/discord", headers=auth_headers,
                                   **({"json": {"enabled": False}} if method == "put" else {}))
    assert resp.status_code == 403


# ── encryption ────────────────────────────────────────────────────────────────

def test_secret_round_trip_and_wrong_key(monkeypatch):
    token = encrypt_secret("client-secret")
    assert token != "client-secret"
    assert decrypt_secret(token) == "client-secret"
    monkeypatch.setattr(settings, "jwt_secret", "rotated-secret")
    assert decrypt_secret(token) is None
    assert decrypt_secret("not-a-fernet-token") is None
    assert decrypt_secret(None) is None


def test_rotated_jwt_secret_reads_as_unset(no_env, monkeypatch):
    row = _row()
    monkeypatch.setattr(settings, "jwt_secret", "rotated-secret")
    config = discord_oauth.resolve_discord_config(row)
    assert config.client_secret is None and config.usable is False
