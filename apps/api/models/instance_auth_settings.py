import uuid
from datetime import datetime
from typing import Optional
from sqlalchemy import JSON, Boolean, DateTime, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
try:
    from ..database import Base
except ImportError:
    from database import Base


class InstanceAuthSettings(Base):
    """Single-row table holding instance-wide sign-in provider settings.

    Created by the first PUT /admin/auth/discord; until then the DISCORD_* env
    vars apply. Each null column falls back to its env var.
    discord_client_secret_encrypted is a Fernet token (services/instance_secrets),
    never the plain secret. discord_allowed_role_ids is a list of role ID strings.
    """
    __tablename__ = "instance_auth_settings"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    discord_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    discord_client_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    discord_client_secret_encrypted: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    discord_server_id: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    discord_allowed_role_ids: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
