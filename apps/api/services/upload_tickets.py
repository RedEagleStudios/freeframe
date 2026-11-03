"""Upload tickets: how an AI agent's shell sends a file it started uploading over MCP.

The MCP tool start_upload creates the version exactly as the browser's
/upload/initiate does, and hands the agent a ticket. The agent's shell (curl,
outside the MCP connection, so the file's bytes never pass through it) trades
the ticket for each part's presigned storage URL at
GET /upload/agent/{ticket}/parts/{n} and PUTs the bytes straight to storage.

A ticket names one version, the user and the OAuth connection it came from, so
it stops working when the connection is disconnected or ends (password change,
deactivation), and when it expires.
"""
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt

from ..config import settings
from .auth_service import decode_token

TICKET_TYPE = "agent_upload"
TICKET_TTL = timedelta(hours=24)


@dataclass(frozen=True)
class UploadTicket:
    user_id: uuid.UUID
    version_id: uuid.UUID
    grant_id: uuid.UUID


def create_ticket(user_id: uuid.UUID, version_id: uuid.UUID, grant_id: uuid.UUID) -> tuple[str, datetime]:
    """A signed ticket for one upload, and when it expires."""
    expires_at = datetime.now(timezone.utc) + TICKET_TTL
    token = jwt.encode(
        {
            "type": TICKET_TYPE,
            "sub": str(user_id),
            "vid": str(version_id),
            "gid": str(grant_id),
            "exp": expires_at,
        },
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )
    return token, expires_at


def read_ticket(token: str) -> Optional[UploadTicket]:
    """The upload a ticket names, or None if it is forged, expired or not a ticket."""
    payload = decode_token(token)
    if not payload or payload.get("type") != TICKET_TYPE:
        return None
    try:
        return UploadTicket(uuid.UUID(payload["sub"]), uuid.UUID(payload["vid"]), uuid.UUID(payload["gid"]))
    except (KeyError, ValueError):
        return None
