"""The MCP SDK's authorization-server hooks, backed by services/oauth_service.py.

The SDK validates each protocol request (client, redirect URI, PKCE, expiry)
and calls these methods for storage and policy. The database is synchronous,
so every call runs in a worker thread with its own session.
"""
import uuid
from collections.abc import Callable
from contextlib import AbstractContextManager, contextmanager
from functools import partial
from typing import Any, Iterator, Optional
from urllib.parse import urlencode

import anyio.to_thread
from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    AuthorizeError,
    RefreshToken,
    TokenError,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from sqlalchemy.orm import Session

from ..config import settings
from ..database import SessionLocal
from . import oauth_service

SessionScope = Callable[[], AbstractContextManager[Session]]


@contextmanager
def default_session_scope() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


class FreeFrameAuthorizationCode(AuthorizationCode):
    token_version: int


class FreeFrameRefreshToken(RefreshToken):
    grant_id: uuid.UUID


def _same_resource(a: str, b: str) -> bool:
    return a.rstrip("/") == b.rstrip("/")


def _token_response(tokens: oauth_service.IssuedTokens) -> OAuthToken:
    return OAuthToken(
        access_token=tokens.access_token,
        expires_in=tokens.expires_in,
        scope=" ".join(tokens.scopes),
        refresh_token=tokens.refresh_token,
    )


class FreeFrameOAuthProvider:
    """Implements mcp.server.auth.provider.OAuthAuthorizationServerProvider."""

    def __init__(self, session_scope: SessionScope = default_session_scope):
        self._session_scope = session_scope

    def _with_db(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        with self._session_scope() as db:
            return fn(db, *args, **kwargs)

    async def _db(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        return await anyio.to_thread.run_sync(partial(self._with_db, fn, *args, **kwargs))

    async def get_client(self, client_id: str) -> Optional[OAuthClientInformationFull]:
        return await self._db(oauth_service.load_client, client_id)

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        await self._db(oauth_service.save_client, client_info)

    async def authorize(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
        # RFC 8707: this server issues tokens for one resource, the MCP endpoint.
        if params.resource and not _same_resource(params.resource, settings.mcp_url):
            raise AuthorizeError(
                error="invalid_target",
                error_description=f"This server only issues tokens for {settings.mcp_url}",
            )
        request_id = await anyio.to_thread.run_sync(partial(
            oauth_service.park_request,
            client,
            scopes=params.scopes,
            code_challenge=params.code_challenge,
            redirect_uri=str(params.redirect_uri),
            redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
            state=params.state,
        ))
        return f"{settings.frontend_url.rstrip('/')}/oauth/consent?{urlencode({'request': request_id})}"

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str,
    ) -> Optional[FreeFrameAuthorizationCode]:
        # Consumed on load: a code is good for one attempt, right or wrong.
        record = await anyio.to_thread.run_sync(oauth_service.take_code, authorization_code)
        if record is None:
            return None
        return FreeFrameAuthorizationCode(
            code=authorization_code,
            scopes=record["scopes"],
            expires_at=record["expires_at"],
            client_id=record["client_id"],
            code_challenge=record["code_challenge"],
            redirect_uri=record["redirect_uri"],
            redirect_uri_provided_explicitly=record["redirect_uri_provided_explicitly"],
            resource=settings.mcp_url,
            subject=record["user_id"],
            token_version=record["token_version"],
        )

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: FreeFrameAuthorizationCode,
    ) -> OAuthToken:
        tokens = await self._db(
            oauth_service.create_grant,
            user_id=uuid.UUID(authorization_code.subject),
            client_id=client.client_id,
            scopes=authorization_code.scopes,
            token_version=authorization_code.token_version,
        )
        if tokens is None:
            raise TokenError(error="invalid_grant", error_description="The account can no longer be connected")
        return _token_response(tokens)

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str,
    ) -> Optional[FreeFrameRefreshToken]:
        grant = await self._db(oauth_service.grant_for_refresh_token, refresh_token)
        if grant is None or grant.client_id != client.client_id:
            return None
        return FreeFrameRefreshToken(
            token=refresh_token,
            client_id=grant.client_id,
            scopes=grant.scopes,
            expires_at=int(grant.refresh_expires_at.timestamp()),
            resource=settings.mcp_url,
            subject=str(grant.user_id),
            grant_id=grant.id,
        )

    async def exchange_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: FreeFrameRefreshToken, scopes: list[str],
    ) -> OAuthToken:
        # The grant keeps its scopes; RFC 6749 §6 wants the new refresh token's
        # scope identical to the old one's, and the response says which it is.
        tokens = await self._db(oauth_service.rotate_grant, refresh_token.grant_id, refresh_token.token)
        if tokens is None:
            raise TokenError(error="invalid_grant", error_description="refresh token does not exist")
        return _token_response(tokens)

    async def load_access_token(self, token: str) -> Optional[AccessToken]:
        grant = await self._db(oauth_service.grant_for_access_token, token)
        if grant is None:
            return None
        return AccessToken(
            token=token,
            client_id=grant.client_id,
            scopes=grant.scopes,
            expires_at=int(grant.access_expires_at.timestamp()),
            resource=settings.mcp_url,
            subject=str(grant.user_id),
        )

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        await self._db(oauth_service.revoke_by_token, token.token)
