import uuid
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, field_validator

from ..models.project import ProjectRole

JoinLinkStatus = Literal["active", "used", "expired", "revoked"]


class ProjectJoinLinkCreate(BaseModel):
    role: ProjectRole = ProjectRole.reviewer
    # Left out: 7 days. null: never expires.
    expires_in_days: Optional[Literal[1, 7, 30]] = 7

    @field_validator("role")
    @classmethod
    def _not_owner(cls, value: ProjectRole) -> ProjectRole:
        if value == ProjectRole.owner:
            raise ValueError("A join link cannot make someone an owner")
        return value


class ProjectJoinLinkResponse(BaseModel):
    """A link as the owner's list shows it. Never carries the token."""
    id: uuid.UUID
    project_id: uuid.UUID
    role: ProjectRole
    status: JoinLinkStatus
    created_at: Optional[datetime] = None
    created_by: uuid.UUID
    created_by_name: Optional[str] = None
    expires_at: Optional[datetime] = None
    used_at: Optional[datetime] = None
    used_by: Optional[uuid.UUID] = None
    used_by_name: Optional[str] = None
    revoked_at: Optional[datetime] = None


class ProjectJoinLinkCreated(ProjectJoinLinkResponse):
    """The one response that carries the token, and the link built from it."""
    token: str
    url: str


class ProjectJoinLinkPreview(BaseModel):
    """What anyone holding the link may learn: the project's name and the role."""
    valid: bool
    project_name: Optional[str] = None
    role: Optional[ProjectRole] = None
    reason: Optional[Literal["expired", "revoked", "used_up", "not_found"]] = None


class ProjectJoinLinkAccepted(BaseModel):
    project_id: uuid.UUID
    role: ProjectRole
