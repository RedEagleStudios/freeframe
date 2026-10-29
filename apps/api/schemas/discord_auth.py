from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

from .auth import TokenResponse
from ..services.discord_oauth import SNOWFLAKE_RE, parse_role_ids


class AuthProvidersResponse(BaseModel):
    discord: bool
    # Sign-in codes by email (EMAIL_CODE_LOGIN); off, the login page offers password sign-in only.
    email_code: bool


class DiscordExchangeRequest(BaseModel):
    ticket: str = Field(..., max_length=256)


class DiscordExchangeResponse(TokenResponse):
    redirect_to: str = "/"


class DiscordAuthSettingsResponse(BaseModel):
    """Never carries the client secret, only whether one is available."""
    enabled: bool
    client_id: Optional[str]
    client_secret_set: bool
    server_id: Optional[str]
    allowed_role_ids: list[str]
    redirect_uri: str
    configured_via: Literal["database", "environment", "none"]


def _snowflake(value: str, what: str) -> str:
    if not SNOWFLAKE_RE.match(value):
        raise ValueError(f"{what} must be a Discord ID (digits only)")
    return value


class DiscordAuthSettingsUpdate(BaseModel):
    """An omitted field is left unchanged. "" or null clears client_id,
    client_secret and server_id, and [] or null clears allowed_role_ids; a
    cleared value falls back to its DISCORD_* env var."""
    enabled: Optional[bool] = None
    client_id: Optional[str] = Field(None, max_length=255)
    client_secret: Optional[str] = Field(None, max_length=1024)
    server_id: Optional[str] = Field(None, max_length=32)
    allowed_role_ids: Optional[list[str]] = Field(None, max_length=100)

    @field_validator("server_id")
    @classmethod
    def _check_server_id(cls, value: Optional[str]) -> Optional[str]:
        value = (value or "").strip()
        return _snowflake(value, "Server ID") if value else None

    @field_validator("allowed_role_ids")
    @classmethod
    def _check_roles(cls, value: Optional[list[str]]) -> Optional[list[str]]:
        roles = parse_role_ids(value)
        return [_snowflake(r, "Role ID") for r in roles]
