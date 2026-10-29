"""Sign in with Discord: provider config, one-time flow state, and Discord calls.

The config an admin saves (InstanceAuthSettings) wins; each value it leaves
unset falls back to its DISCORD_* env var. Modelled on Outline's Discord
plugin: with a server (guild) ID configured, only its members (holding one of
the allowed roles, when roles are set) may sign in, and a member with no
account is given one. Without a server ID, Discord only signs in existing
accounts and pending invitees.
"""
import json
import logging
import re
import secrets
from dataclasses import dataclass, field
from typing import Literal, Optional
from urllib.parse import urlencode

import httpx
from sqlalchemy.orm import Session

from ..config import settings
from ..models.instance_auth_settings import InstanceAuthSettings
from .instance_secrets import decrypt_secret
from .redis_service import get_redis

logger = logging.getLogger(__name__)

DISCORD_AUTHORIZE_URL = "https://discord.com/oauth2/authorize"
DISCORD_TOKEN_URL = "https://discord.com/api/oauth2/token"
DISCORD_API = "https://discord.com/api"
DISCORD_HTTP_TIMEOUT = httpx.Timeout(10.0, connect=5.0)

STATE_COOKIE = "ff_discord_state"
STATE_PREFIX = "discord_oauth_state:"
STATE_TTL_SECONDS = 600
TICKET_PREFIX = "discord_oauth_ticket:"
TICKET_TTL_SECONDS = 60

# Discord IDs are snowflakes: unsigned 64-bit integers sent as strings.
SNOWFLAKE_RE = re.compile(r"^\d{1,20}$")


class DiscordOAuthError(Exception):
    """Discord refused the code or answered with something unusable."""


@dataclass(frozen=True)
class DiscordConfig:
    enabled: bool
    client_id: Optional[str]
    client_secret: Optional[str]
    server_id: Optional[str]
    allowed_role_ids: list[str] = field(default_factory=list)
    configured_via: Literal["database", "environment", "none"] = "none"

    @property
    def usable(self) -> bool:
        return bool(self.enabled and self.client_id and self.client_secret)

    @property
    def scope(self) -> str:
        return "identify email guilds.members.read" if self.server_id else "identify email"


def _clean(value: Optional[str]) -> Optional[str]:
    value = (value or "").strip()
    return value or None


def parse_role_ids(value) -> list[str]:
    """Role IDs from a list or a comma/whitespace-separated string, deduplicated."""
    if not value:
        return []
    items = value if isinstance(value, list) else re.split(r"[\s,]+", str(value))
    return list(dict.fromkeys(str(v).strip() for v in items if str(v).strip()))


def get_auth_settings_row(db: Session) -> Optional[InstanceAuthSettings]:
    return db.query(InstanceAuthSettings).first()


def resolve_discord_config(row: Optional[InstanceAuthSettings]) -> DiscordConfig:
    env_id = _clean(settings.discord_client_id)
    env_secret = _clean(settings.discord_client_secret)
    db_id = _clean(row.discord_client_id) if row else None
    db_secret = decrypt_secret(row.discord_client_secret_encrypted) if row else None
    db_server = _clean(row.discord_server_id) if row else None
    db_roles = parse_role_ids(row.discord_allowed_role_ids) if row else []

    if row is not None and (db_id or row.discord_client_secret_encrypted):
        via = "database"
    elif env_id or env_secret:
        via = "environment"
    else:
        via = "none"
    return DiscordConfig(
        enabled=row.discord_enabled if row is not None else settings.discord_login_enabled,
        client_id=db_id or env_id,
        client_secret=db_secret or env_secret,
        server_id=db_server or _clean(settings.discord_server_id),
        allowed_role_ids=db_roles or parse_role_ids(settings.discord_server_roles),
        configured_via=via,
    )


def load_discord_config(db: Session) -> DiscordConfig:
    return resolve_discord_config(get_auth_settings_row(db))


def discord_redirect_uri() -> str:
    """The callback URL registered on the Discord application.

    The API is served at the origin root (`/api`) even on a sub-path install
    (FRONTEND_URL already carries the sub-path), so the default uses
    FRONTEND_URL's origin.
    """
    explicit = _clean(settings.discord_oauth_redirect_uri)
    if explicit:
        return explicit
    return f"{settings.frontend_origin.rstrip('/')}/api/auth/discord/callback"


def frontend_url(path: str) -> str:
    """A link into the web app; FRONTEND_URL already carries any sub-path."""
    return f"{settings.frontend_url.rstrip('/')}{path}"


def safe_internal_path(value: Optional[str]) -> str:
    """Same rules as the web app's safeInternalPath: an app-absolute path or '/'."""
    if not value or not value.startswith("/"):
        return "/"
    if value.startswith("//") or value.startswith("/\\"):
        return "/"
    if any(ord(c) < 0x20 or ord(c) == 0x7F or c.isspace() for c in value):
        return "/"
    return value


# ── One-time flow state ───────────────────────────────────────────────────────

def begin_flow(config: DiscordConfig, from_path: Optional[str]) -> tuple[str, str]:
    """Store a fresh state and return (state nonce, Discord authorization URL)."""
    state = secrets.token_urlsafe(32)
    get_redis().setex(
        f"{STATE_PREFIX}{state}",
        STATE_TTL_SECONDS,
        json.dumps({"from": safe_internal_path(from_path)}),
    )
    query = urlencode({
        "response_type": "code",
        "client_id": config.client_id,
        "redirect_uri": discord_redirect_uri(),
        "scope": config.scope,
        "state": state,
        "prompt": "none",
    })
    return state, f"{DISCORD_AUTHORIZE_URL}?{query}"


def consume_flow(state: str) -> Optional[dict]:
    """The stored flow for `state`, deleted as it is read, or None."""
    raw = get_redis().getdel(f"{STATE_PREFIX}{state}")
    return json.loads(raw) if raw else None


def issue_ticket(user_id: str, from_path: str) -> str:
    ticket = secrets.token_urlsafe(32)
    get_redis().setex(
        f"{TICKET_PREFIX}{ticket}",
        TICKET_TTL_SECONDS,
        json.dumps({"user_id": user_id, "from": safe_internal_path(from_path)}),
    )
    return ticket


def consume_ticket(ticket: str) -> Optional[dict]:
    raw = get_redis().getdel(f"{TICKET_PREFIX}{ticket}")
    return json.loads(raw) if raw else None


# ── Discord HTTP ──────────────────────────────────────────────────────────────

def exchange_code(config: DiscordConfig, code: str) -> str:
    """Trade an authorization code for an access token."""
    try:
        resp = httpx.post(
            DISCORD_TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": discord_redirect_uri(),
                "client_id": config.client_id,
                "client_secret": config.client_secret,
            },
            headers={"Accept": "application/json"},
            timeout=DISCORD_HTTP_TIMEOUT,
        )
    except httpx.HTTPError as exc:
        raise DiscordOAuthError(f"token request failed: {exc}") from exc
    if resp.status_code != 200:
        # Discord's error body names the problem (invalid_grant, invalid_client)
        # and never echoes the secret.
        raise DiscordOAuthError(f"token endpoint answered {resp.status_code}: {resp.text[:300]}")
    try:
        token = resp.json()["access_token"]
    except (ValueError, KeyError, TypeError) as exc:
        raise DiscordOAuthError("token endpoint sent no access_token") from exc
    if not isinstance(token, str) or not token:
        raise DiscordOAuthError("token endpoint sent no access_token")
    return token


def _get_json(path: str, access_token: str) -> httpx.Response:
    try:
        return httpx.get(
            f"{DISCORD_API}{path}",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=DISCORD_HTTP_TIMEOUT,
        )
    except httpx.HTTPError as exc:
        raise DiscordOAuthError(f"GET {path} failed: {exc}") from exc


def _json_object(resp: httpx.Response, what: str) -> dict:
    try:
        body = resp.json()
    except ValueError as exc:
        raise DiscordOAuthError(f"{what} sent invalid JSON") from exc
    if not isinstance(body, dict):
        raise DiscordOAuthError(f"{what} sent invalid JSON")
    return body


def fetch_profile(access_token: str) -> dict:
    """The signed-in Discord user: id, username, global_name, email, verified."""
    resp = _get_json("/users/@me", access_token)
    if resp.status_code != 200:
        raise DiscordOAuthError(f"/users/@me answered {resp.status_code}")
    return _json_object(resp, "/users/@me")


def fetch_guild_member(access_token: str, server_id: str) -> Optional[dict]:
    """The user's member object in `server_id` (nick, roles), or None if not a member."""
    resp = _get_json(f"/users/@me/guilds/{server_id}/member", access_token)
    if resp.status_code in (403, 404):
        return None
    if resp.status_code != 200:
        raise DiscordOAuthError(f"guild member lookup answered {resp.status_code}")
    return _json_object(resp, "guild member lookup")
