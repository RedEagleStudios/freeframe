"""The signed-in side of MCP OAuth: the consent page's calls and the user's
list of connected apps. The protocol endpoints themselves (/authorize,
/token, /register, /revoke) are the MCP SDK's; see services/oauth_service.py."""
import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db
from ..middleware.auth import get_current_user
from ..models.user import User
from ..services import oauth_service
from ..services.mcp_server import mcp_disabled_reason

router = APIRouter(prefix="/oauth", tags=["oauth"])

REQUEST_GONE = "This connection request has expired or was already answered. Start the connection again from your app."


class ScopeInfo(BaseModel):
    name: str
    description: str


class McpServerInfo(BaseModel):
    mcp_url: str
    enabled: bool
    # Why the server is off with this configuration (see mcp_disabled_reason).
    disabled_reason: Optional[str] = None


class ConsentRequest(BaseModel):
    client_name: str
    client_uri: Optional[str]
    redirect_uri: str
    scopes: list[ScopeInfo]


class ConsentDecision(BaseModel):
    # Off grants a read-only connection.
    allow_write: bool = True


class ConsentRedirect(BaseModel):
    redirect_url: str


class ConnectedApp(BaseModel):
    id: uuid.UUID
    client_name: str
    scopes: list[str]
    created_at: datetime
    last_used_at: Optional[datetime]


def _scope_info() -> list[ScopeInfo]:
    return [ScopeInfo(name=n, description=d) for n, d in oauth_service.SCOPE_DESCRIPTIONS.items()]


@router.get("/server-info", response_model=McpServerInfo)
def get_server_info(current_user: User = Depends(get_current_user)):
    reason = mcp_disabled_reason()
    return McpServerInfo(mcp_url=settings.mcp_url, enabled=reason is None, disabled_reason=reason)


@router.get("/requests/{request_id}", response_model=ConsentRequest)
def get_consent_request(request_id: str, current_user: User = Depends(get_current_user)):
    data = oauth_service.get_request(request_id)
    if data is None:
        raise HTTPException(status_code=404, detail=REQUEST_GONE)
    return ConsentRequest(
        client_name=data["client_name"] or "An unnamed app",
        client_uri=data["client_uri"],
        redirect_uri=data["redirect_uri"],
        scopes=_scope_info(),
    )


@router.post("/requests/{request_id}/approve", response_model=ConsentRedirect)
def approve_consent_request(
    request_id: str, body: ConsentDecision, current_user: User = Depends(get_current_user),
):
    url = oauth_service.approve_request(request_id, current_user, body.allow_write)
    if url is None:
        raise HTTPException(status_code=404, detail=REQUEST_GONE)
    return ConsentRedirect(redirect_url=url)


@router.post("/requests/{request_id}/deny", response_model=ConsentRedirect)
def deny_consent_request(request_id: str, current_user: User = Depends(get_current_user)):
    url = oauth_service.deny_request(request_id)
    if url is None:
        raise HTTPException(status_code=404, detail=REQUEST_GONE)
    return ConsentRedirect(redirect_url=url)


@router.get("/grants", response_model=list[ConnectedApp])
def list_connected_apps(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return [
        ConnectedApp(
            id=grant.id,
            client_name=client.client_info.get("client_name") or "An unnamed app",
            scopes=grant.scopes,
            created_at=grant.created_at,
            last_used_at=grant.last_used_at,
        )
        for grant, client in oauth_service.list_user_grants(db, current_user.id)
    ]


@router.delete("/grants/{grant_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_connected_app(
    grant_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user),
):
    if not oauth_service.revoke_user_grant(db, current_user.id, grant_id):
        raise HTTPException(status_code=404, detail="Connection not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
