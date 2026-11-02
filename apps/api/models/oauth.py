import uuid
from datetime import datetime
from typing import Optional
from sqlalchemy import String, DateTime, ForeignKey, Integer, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
try:
    from ..database import Base
except ImportError:
    from database import Base


class OAuthClient(Base):
    """An OAuth client (an MCP app such as Claude or Cursor) registered through
    Dynamic Client Registration (RFC 7591). Registration is unauthenticated by
    design; a client gets nothing until a signed-in user approves it."""
    __tablename__ = "oauth_clients"
    client_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    # The registration as the SDK models it (OAuthClientInformationFull): name,
    # redirect URIs, auth method, and the client secret for confidential clients.
    client_info: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class OAuthGrant(Base):
    """One user's approval of one client: the connection Settings → AI access lists.

    Holds the hashes of the current access and refresh token. A refresh replaces
    both (rotation), so a refresh token works once. Revoking sets `revoked_at`;
    a password change (token_version) or deactivation also ends the grant."""
    __tablename__ = "oauth_grants"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    client_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("oauth_clients.client_id", ondelete="CASCADE"), nullable=False,
    )
    scopes: Mapped[list] = mapped_column(JSONB, nullable=False)
    # The user's token_version when they approved; a password change bumps it.
    token_version: Mapped[int] = mapped_column(Integer, nullable=False)
    access_token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    access_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    refresh_token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    refresh_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # Last token issue or refresh. Agents refresh about hourly while in use.
    last_used_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
