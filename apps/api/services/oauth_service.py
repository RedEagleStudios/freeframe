"""OAuth 2.1 storage and rules for the MCP server. FreeFrame is its own
authorization server; the MCP SDK runs the protocol endpoints (/register,
/authorize, /token, /revoke) and calls services/mcp_auth.py, which lands here.

The flow:
1. An MCP client registers itself (Dynamic Client Registration).
2. It sends the user's browser to /authorize. The request is parked in Redis,
   and the browser goes on to the web app's consent page.
3. The signed-in user approves or denies there (routers/oauth.py). Approving
   turns the parked request into a one-time code, also in Redis, and sends the
   browser back to the client.
4. The client trades the code (with its PKCE verifier) for an access token and
   a refresh token, kept hashed on an OAuthGrant.
5. Every refresh replaces both tokens, so a refresh token works once.

Parked requests and codes are short-lived and live in Redis. Clients and grants
live in Postgres.
"""
import hashlib
import json
import secrets
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from mcp.server.auth.provider import construct_redirect_uri
from mcp.shared.auth import OAuthClientInformationFull
from sqlalchemy import or_, update
from sqlalchemy.orm import Session

from ..config import settings
from ..models.oauth import OAuthClient, OAuthGrant
from ..models.user import User, UserStatus
from . import redis_service

SCOPE_READ = "freeframe:read"
SCOPE_WRITE = "freeframe:write"
# In the order the consent page lists them.
SCOPE_DESCRIPTIONS = {
    SCOPE_READ: "See your projects, folders, files and comments, and download files",
    SCOPE_WRITE: "Upload files, post comments, and approve or reject files, as you",
}
ALL_SCOPES = list(SCOPE_DESCRIPTIONS)

REQUEST_TTL_SECONDS = 600
CODE_TTL_SECONDS = 300
_REQUEST_KEY = "oauth:request:"
_CODE_KEY = "oauth:code:"
# Recognisable prefixes, so a leaked token is easy to spot (and to scan for).
ACCESS_TOKEN_PREFIX = "ffat_"
REFRESH_TOKEN_PREFIX = "ffrt_"


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ─── Clients ───────────────────────────────────────────────────────────────────

def save_client(db: Session, client: OAuthClientInformationFull) -> None:
    db.add(OAuthClient(client_id=client.client_id, client_info=client.model_dump(mode="json", exclude_none=True)))
    db.commit()


def load_client(db: Session, client_id: str) -> Optional[OAuthClientInformationFull]:
    row = db.get(OAuthClient, client_id)
    return OAuthClientInformationFull.model_validate(row.client_info) if row else None


# ─── Authorization requests and codes (Redis) ──────────────────────────────────

def park_request(
    client: OAuthClientInformationFull,
    *,
    scopes: Optional[list[str]],
    code_challenge: str,
    redirect_uri: str,
    redirect_uri_provided_explicitly: bool,
    state: Optional[str],
) -> str:
    """Hold a validated /authorize request until the user decides. Returns its id."""
    request_id = secrets.token_urlsafe(32)
    data = {
        "client_id": client.client_id,
        "client_name": client.client_name or "",
        "client_uri": str(client.client_uri) if client.client_uri else None,
        "requested_scopes": scopes,
        "code_challenge": code_challenge,
        "redirect_uri": redirect_uri,
        "redirect_uri_provided_explicitly": redirect_uri_provided_explicitly,
        "state": state,
    }
    redis_service.get_redis().setex(_REQUEST_KEY + request_id, REQUEST_TTL_SECONDS, json.dumps(data))
    return request_id


def get_request(request_id: str) -> Optional[dict]:
    raw = redis_service.get_redis().get(_REQUEST_KEY + request_id)
    return json.loads(raw) if raw else None


def _take(key: str) -> Optional[dict]:
    """Read and delete in one step, so a request or a code is used at most once."""
    raw = redis_service.get_redis().getdel(key)
    return json.loads(raw) if raw else None


def granted_scopes(allow_write: bool) -> list[str]:
    """The consent page decides: read always, write if the user left it on.

    Clients ask for what the protected-resource metadata advertises, which is
    only the scope every call needs, so what they request says little. RFC 6749
    §3.3 lets the server grant per the user's instructions, and the token
    response's `scope` tells the client what it got."""
    return [SCOPE_READ, SCOPE_WRITE] if allow_write else [SCOPE_READ]


def approve_request(request_id: str, user: User, allow_write: bool) -> Optional[str]:
    """Turn a parked request into a one-time code for `user`.

    Returns the URL to send the browser to (the client's redirect URI with the
    code), or None if the request is unknown, expired or already decided."""
    data = _take(_REQUEST_KEY + request_id)
    if data is None:
        return None
    code = secrets.token_urlsafe(32)
    record = {
        **data,
        "scopes": granted_scopes(allow_write),
        "user_id": str(user.id),
        "token_version": user.token_version or 1,
        "expires_at": time.time() + CODE_TTL_SECONDS,
    }
    redis_service.get_redis().setex(_CODE_KEY + code, CODE_TTL_SECONDS, json.dumps(record))
    return construct_redirect_uri(data["redirect_uri"], code=code, state=data["state"], iss=settings.api_base_url)


def deny_request(request_id: str) -> Optional[str]:
    """Drop a parked request. Returns the client's redirect URI carrying
    `access_denied`, or None if the request is unknown or already decided."""
    data = _take(_REQUEST_KEY + request_id)
    if data is None:
        return None
    return construct_redirect_uri(
        data["redirect_uri"],
        error="access_denied",
        error_description="The user declined the connection",
        state=data["state"],
        iss=settings.api_base_url,
    )


def take_code(code: str) -> Optional[dict]:
    return _take(_CODE_KEY + code)


# ─── Grants and tokens (Postgres) ──────────────────────────────────────────────

@dataclass(frozen=True)
class IssuedTokens:
    access_token: str
    refresh_token: str
    expires_in: int
    scopes: list[str]


@dataclass(frozen=True)
class LiveGrant:
    id: uuid.UUID
    user_id: uuid.UUID
    client_id: str
    scopes: list[str]
    access_expires_at: datetime
    refresh_expires_at: datetime


def _fresh_token_columns(now: datetime) -> tuple[str, str, dict]:
    access = ACCESS_TOKEN_PREFIX + secrets.token_urlsafe(32)
    refresh = REFRESH_TOKEN_PREFIX + secrets.token_urlsafe(32)
    columns = {
        "access_token_hash": hash_token(access),
        "access_expires_at": now + timedelta(minutes=settings.mcp_access_token_expire_minutes),
        "refresh_token_hash": hash_token(refresh),
        "refresh_expires_at": now + timedelta(days=settings.mcp_refresh_token_expire_days),
        "last_used_at": now,
    }
    return access, refresh, columns


def _user_may_hold(user: Optional[User], token_version: int) -> bool:
    """A grant lives only as long as the account does: not deleted, not
    deactivated, and no password change (token_version) since the approval."""
    return (
        user is not None
        and user.deleted_at is None
        and user.status != UserStatus.deactivated
        and (user.token_version or 1) == token_version
    )


def create_grant(
    db: Session, *, user_id: uuid.UUID, client_id: str, scopes: list[str], token_version: int,
) -> Optional[IssuedTokens]:
    """Issue the first tokens for an approved code. None if the account can no longer hold them."""
    if not _user_may_hold(db.get(User, user_id), token_version):
        return None
    access, refresh, columns = _fresh_token_columns(_now())
    db.add(OAuthGrant(user_id=user_id, client_id=client_id, scopes=scopes, token_version=token_version, **columns))
    db.commit()
    return IssuedTokens(access, refresh, settings.mcp_access_token_expire_minutes * 60, scopes)


def rotate_grant(db: Session, grant_id: uuid.UUID, refresh_token: str) -> Optional[IssuedTokens]:
    """Replace a grant's tokens, if `refresh_token` is still its current one.

    The compare-and-set makes a refresh token single-use even when two
    refreshes race: only one of them matches the hash."""
    access, refresh, columns = _fresh_token_columns(_now())
    row = db.execute(
        update(OAuthGrant)
        .where(
            OAuthGrant.id == grant_id,
            OAuthGrant.refresh_token_hash == hash_token(refresh_token),
            OAuthGrant.revoked_at.is_(None),
        )
        .values(**columns)
        .returning(OAuthGrant.scopes)
    ).first()
    db.commit()
    if row is None:
        return None
    return IssuedTokens(access, refresh, settings.mcp_access_token_expire_minutes * 60, list(row.scopes))


def _live_grant(db: Session, *conditions) -> Optional[LiveGrant]:
    row = (
        db.query(OAuthGrant, User)
        .join(User, User.id == OAuthGrant.user_id)
        .filter(OAuthGrant.revoked_at.is_(None), *conditions)
        .first()
    )
    if row is None:
        return None
    grant, user = row
    if not _user_may_hold(user, grant.token_version):
        return None
    return LiveGrant(
        grant.id, grant.user_id, grant.client_id, list(grant.scopes), grant.access_expires_at, grant.refresh_expires_at,
    )


def grant_for_access_token(db: Session, token: str) -> Optional[LiveGrant]:
    if not token.startswith(ACCESS_TOKEN_PREFIX):
        return None
    return _live_grant(db, OAuthGrant.access_token_hash == hash_token(token), OAuthGrant.access_expires_at > _now())


def grant_for_refresh_token(db: Session, token: str) -> Optional[LiveGrant]:
    if not token.startswith(REFRESH_TOKEN_PREFIX):
        return None
    return _live_grant(db, OAuthGrant.refresh_token_hash == hash_token(token), OAuthGrant.refresh_expires_at > _now())


def live_grant(db: Session, grant_id: uuid.UUID) -> Optional[LiveGrant]:
    """A connection that has not been revoked or lapsed, held by an account that may still hold it."""
    return _live_grant(db, OAuthGrant.id == grant_id, OAuthGrant.refresh_expires_at > _now())


def revoke_by_token(db: Session, token: str) -> None:
    """End the grant a token (access or refresh) belongs to. Unknown tokens are ignored."""
    hashed = hash_token(token)
    db.execute(
        update(OAuthGrant)
        .where(
            or_(OAuthGrant.access_token_hash == hashed, OAuthGrant.refresh_token_hash == hashed),
            OAuthGrant.revoked_at.is_(None),
        )
        .values(revoked_at=_now())
    )
    db.commit()


def list_user_grants(db: Session, user_id: uuid.UUID) -> list[tuple[OAuthGrant, OAuthClient]]:
    """The user's connections that can still refresh, newest first."""
    return (
        db.query(OAuthGrant, OAuthClient)
        .join(OAuthClient, OAuthClient.client_id == OAuthGrant.client_id)
        .filter(
            OAuthGrant.user_id == user_id,
            OAuthGrant.revoked_at.is_(None),
            OAuthGrant.refresh_expires_at > _now(),
        )
        .order_by(OAuthGrant.created_at.desc())
        .all()
    )


def revoke_user_grant(db: Session, user_id: uuid.UUID, grant_id: uuid.UUID) -> bool:
    """Revoke one of the user's own connections. False if it isn't theirs or is already revoked."""
    result = db.execute(
        update(OAuthGrant)
        .where(OAuthGrant.id == grant_id, OAuthGrant.user_id == user_id, OAuthGrant.revoked_at.is_(None))
        .values(revoked_at=_now())
    )
    db.commit()
    return result.rowcount > 0
