"""Sign in with Discord (public endpoints).

Flow: /auth/discord/start -> Discord -> /auth/discord/callback -> web app
/auth/discord/callback?ticket=... -> POST /auth/discord/exchange -> tokens.
Tokens never travel in a URL; the ticket is single-use and lives 60 seconds.

Account rules: the Discord email must be verified. With a server ID set, the
user must be a member of that server (holding an allowed role, when roles are
set), checked on every sign-in, and a member with no account gets one. With no
server ID, only existing accounts and pending invitees can sign in.
"""
import logging
import secrets
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..database import get_db
from ..middleware.rate_limit import rate_limit
from ..models.user import User, UserStatus
from ..schemas.discord_auth import AuthProvidersResponse, DiscordExchangeRequest, DiscordExchangeResponse
from ..services import discord_oauth
from ..services.auth_service import (
    create_access_token, create_refresh_token, get_email_owner, get_user_by_email, get_user_by_id,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])


def _login_error(code: str) -> RedirectResponse:
    resp = RedirectResponse(discord_oauth.frontend_url(f"/login?error={code}"), status_code=302)
    resp.delete_cookie(discord_oauth.STATE_COOKIE, path="/")
    return resp


def _display_name(profile: dict, member: Optional[dict]) -> str:
    for candidate in ((member or {}).get("nick"), profile.get("global_name"), profile.get("username")):
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()[:255]
    return "Discord user"


@router.get("/providers", response_model=AuthProvidersResponse)
def auth_providers(db: Session = Depends(get_db)):
    """Which sign-in providers the login page should offer."""
    return AuthProvidersResponse(discord=discord_oauth.load_discord_config(db).usable)


@router.get("/discord/start", dependencies=[Depends(rate_limit("discord_start", 20, 600))])
def discord_start(
    from_path: Optional[str] = Query(None, alias="from"),
    db: Session = Depends(get_db),
):
    config = discord_oauth.load_discord_config(db)
    if not config.usable:
        return _login_error("discord_not_configured")
    state, url = discord_oauth.begin_flow(config, from_path)
    resp = RedirectResponse(url, status_code=302)
    resp.set_cookie(
        discord_oauth.STATE_COOKIE,
        state,
        max_age=discord_oauth.STATE_TTL_SECONDS,
        path="/",
        httponly=True,
        samesite="lax",
        secure=discord_oauth.discord_redirect_uri().startswith("https://"),
    )
    return resp


@router.get("/discord/callback", dependencies=[Depends(rate_limit("discord_callback", 30, 600))])
def discord_callback(
    request: Request,
    code: Optional[str] = None,
    state: Optional[str] = None,
    error: Optional[str] = None,
    db: Session = Depends(get_db),
):
    # Burn the state whatever happens next, so it can never be replayed.
    flow = discord_oauth.consume_flow(state) if state else None
    cookie = request.cookies.get(discord_oauth.STATE_COOKIE)
    if (
        error or not code or not flow or not cookie
        or not secrets.compare_digest(cookie.encode(), state.encode())
    ):
        if error:
            logger.info("Discord sign-in returned error=%s", error)
        return _login_error("discord_failed")

    config = discord_oauth.load_discord_config(db)
    if not config.usable:
        return _login_error("discord_not_configured")

    try:
        access_token = discord_oauth.exchange_code(config, code)
        profile = discord_oauth.fetch_profile(access_token)
        member = (
            discord_oauth.fetch_guild_member(access_token, config.server_id)
            if config.server_id else None
        )
    except discord_oauth.DiscordOAuthError as exc:
        logger.warning("Discord sign-in failed: %s", exc)
        return _login_error("discord_failed")

    email = profile.get("email")
    if not isinstance(email, str) or not email.strip() or profile.get("verified") is not True:
        return _login_error("discord_email_unverified")
    email = email.strip()

    # Server membership and roles are checked on every sign-in, so leaving the
    # server or losing the role locks the account out of Discord sign-in.
    if config.server_id:
        if member is None:
            return _login_error("discord_not_in_server")
        if config.allowed_role_ids:
            roles = member.get("roles") or []
            if not isinstance(roles, list) or not set(map(str, roles)) & set(config.allowed_role_ids):
                return _login_error("discord_missing_role")

    user = get_user_by_email(db, email)
    if user is None:
        if not config.server_id:
            return _login_error("discord_no_account")
        # A deleted account keeps its address (the unique constraint still
        # covers it), and two live accounts differing only by case make the
        # match ambiguous: neither gets a new account.
        owner = get_email_owner(db, email)
        if owner is not None:
            return _login_error(
                "discord_account_disabled" if owner.deleted_at is not None else "discord_failed"
            )
        user = User(
            id=uuid.uuid4(),
            email=email.lower(),
            name=_display_name(profile, member),
            status=UserStatus.active,
            email_verified=True,
            is_superadmin=False,
        )
        db.add(user)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            return _login_error("discord_failed")
        db.refresh(user)
        logger.info("Discord sign-in created an account for user %s", user.id)
    elif user.status == UserStatus.deactivated:
        return _login_error("discord_account_disabled")
    elif user.status == UserStatus.pending_invite and not user.email_verified:
        # An invitee who has not accepted yet: accept it here as the invite
        # link would, minus the password, keeping the name the admin gave.
        expires = user.invite_token_expires_at
        if not user.invite_token or (expires and expires < datetime.now(timezone.utc)):
            return _login_error("discord_invite_expired")
        user.status = UserStatus.active
        user.email_verified = True
        user.invite_token = None
        user.invite_token_expires_at = None
        db.commit()
    else:
        # As a sign-in code does: the address is proven, and a self-verifying
        # account becomes active.
        user.email_verified = True
        if user.status == UserStatus.pending_verification:
            user.status = UserStatus.active
        db.commit()

    ticket = discord_oauth.issue_ticket(str(user.id), flow.get("from") or "/")
    resp = RedirectResponse(
        discord_oauth.frontend_url(f"/auth/discord/callback?ticket={ticket}"), status_code=302,
    )
    resp.delete_cookie(discord_oauth.STATE_COOKIE, path="/")
    return resp


@router.post(
    "/discord/exchange",
    response_model=DiscordExchangeResponse,
    dependencies=[Depends(rate_limit("discord_exchange", 20, 600))],
)
def discord_exchange(body: DiscordExchangeRequest, db: Session = Depends(get_db)):
    invalid = HTTPException(status_code=400, detail="Invalid or expired sign-in ticket")
    data = discord_oauth.consume_ticket(body.ticket) if body.ticket else None
    if not data:
        raise invalid
    try:
        user_id = uuid.UUID(data["user_id"])
    except (KeyError, ValueError, TypeError):
        raise invalid
    user = get_user_by_id(db, user_id)
    if not user or user.status == UserStatus.deactivated:
        raise invalid
    return DiscordExchangeResponse(
        access_token=create_access_token(str(user.id), token_version=user.token_version),
        refresh_token=create_refresh_token(str(user.id), token_version=user.token_version),
        needs_password=False,
        redirect_to=discord_oauth.safe_internal_path(data.get("from")),
    )
