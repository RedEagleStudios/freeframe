from pydantic import BaseModel, Field
import uuid
from datetime import datetime
from ..models.project import ProjectType, ProjectRole
from ..config import settings

class ProjectCreate(BaseModel):
    name: str
    description: str | None = None
    project_type: ProjectType = ProjectType.personal

class ProjectUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    is_public: bool | None = None
    # Owner-only, like every field here (PATCH /projects/{id}).
    streaming_mode: bool | None = None

class ProjectResponse(BaseModel):
    id: uuid.UUID
    name: str
    description: str | None
    project_type: ProjectType
    created_by: uuid.UUID
    created_at: datetime
    poster_url: str | None = None
    is_public: bool = False
    streaming_mode: bool = False
    # Instance-wide STREAMING_ORIGINAL_RETENTION_DAYS, so the settings page can say
    # when originals go. 0 means the deletion is switched off on this server.
    streaming_retention_days: int = Field(
        default_factory=lambda: settings.streaming_original_retention_days
    )
    asset_count: int = 0
    storage_bytes: int = 0
    member_count: int = 0
    role: ProjectRole | None = None
    model_config = {"from_attributes": True}

class ProjectOwner(BaseModel):
    id: uuid.UUID
    name: str
    email: str

class ProjectListItem(ProjectResponse):
    """A ``GET /projects`` row: the project plus its live owners, so a superadmin
    browsing every project can tell whose it is."""
    owners: list[ProjectOwner] = []

class ProjectMemberResponse(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    user_id: uuid.UUID
    role: ProjectRole
    invited_at: datetime | None = None  # when the member joined the project
    model_config = {"from_attributes": True}

class AddProjectMemberRequest(BaseModel):
    user_id: uuid.UUID
    role: ProjectRole = ProjectRole.viewer

class UpdateProjectMemberRequest(BaseModel):
    role: ProjectRole
