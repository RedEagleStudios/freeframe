"""Admin endpoints for user management."""

from datetime import datetime, timedelta, timezone
import secrets

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
import uuid

from ..database import get_db
from ..middleware.auth import get_current_user
from ..models.instance_auth_settings import InstanceAuthSettings
from ..models.project import Project, ProjectMember, ProjectRole
from ..models.user import User, UserStatus
from ..schemas.auth import UserResponse, AdminUserResponse, UpdateUserRoleRequest
from ..schemas.project import ProjectResponse
from .users import require_admin
from .projects import build_project_list
from ..tasks.celery_app import send_task_safe
from ..tasks.cleanup_tasks import cleanup_soft_deleted
from ..schemas.admin import PurgeStartResponse
from ..schemas.discord_auth import DiscordAuthSettingsResponse, DiscordAuthSettingsUpdate
from ..config import settings
from ..services import discord_oauth
from ..services.instance_secrets import encrypt_secret

router = APIRouter(prefix="/admin", tags=["admin"])


# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.get("/users", response_model=list[AdminUserResponse])
def list_all_users(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List all users in the system. Only accessible by admins."""
    if not current_user.is_superadmin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only admins can access this endpoint"
        )

    users = db.query(User).filter(User.deleted_at.is_(None)).all()
    return users

@router.patch("/users/{user_id}/deactivate", response_model=AdminUserResponse)
def deactivate_user(
    user_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Deactivate a user. Admins cannot deactivate themselves."""
    if not current_user.is_superadmin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only admins can deactivate users"
        )

    # Prevent admin from deactivating themselves
    if user_id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You cannot deactivate yourself"
        )

    user = db.query(User).filter(User.id == user_id, User.deleted_at.is_(None)).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    user.status = UserStatus.deactivated
    # Deactivating a pending invitee withdraws the invite, so a later
    # Reactivate doesn't bring the old link back to life.
    user.invite_token = None
    user.invite_token_expires_at = None
    db.commit()
    db.refresh(user)
    return user

@router.patch("/users/{user_id}/reactivate", response_model=AdminUserResponse)
def reactivate_user(
    user_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Reactivate a deactivated user. Only accessible by admins."""
    if not current_user.is_superadmin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only admins can reactivate users"
        )

    user = db.query(User).filter(User.id == user_id, User.deleted_at.is_(None)).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if user.password_hash is None and not user.email_verified:
        # An invitee who never accepted has no way in once deactivation cleared
        # their link, so they go back to pending with a fresh one to copy.
        user.status = UserStatus.pending_invite
        user.invite_token = secrets.token_urlsafe(48)
        user.invite_token_expires_at = datetime.now(timezone.utc) + timedelta(days=7)
    else:
        user.status = UserStatus.active
    db.commit()
    db.refresh(user)
    return user

@router.patch("/users/{user_id}/role", response_model=AdminUserResponse)
def update_user_role(
    user_id: uuid.UUID,
    body: UpdateUserRoleRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Promote or demote a user to/from admin role. Only accessible by admins."""
    if not current_user.is_superadmin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only admins can change user roles"
        )

    # Prevent admin from removing their own admin role
    if user_id == current_user.id and not body.is_admin:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You cannot remove your own admin role"
        )

    user = db.query(User).filter(User.id == user_id, User.deleted_at.is_(None)).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    user.is_superadmin = body.is_admin
    db.commit()
    db.refresh(user)
    return user

@router.post("/purge", response_model=PurgeStartResponse, status_code=status.HTTP_202_ACCEPTED)
def purge_now(current_user: User = Depends(require_admin)):
    """Trigger the retention-window garbage collector to run now, in the background.

    Superadmin only. Enqueues the same `cleanup_soft_deleted` task the daily beat runs, so the
    request returns immediately instead of blocking on a potentially long cascade + S3 deletes.
    Reclaimed counts are logged by the worker. If a purge is already running (e.g. the daily beat),
    the advisory lock serializes purges and this enqueued run is skipped rather than double-cascading,
    so a 202 here means "enqueued", not "a fresh run happened".
    """
    send_task_safe(cleanup_soft_deleted)
    return PurgeStartResponse(
        status="started",
        detail="Retention garbage collection is running in the background; see worker logs for reclaimed counts.",
    )


# ── Project access ─────────────────────────────────────────────────────────────

@router.post("/projects/{project_id}/join", response_model=ProjectResponse)
def join_project_as_owner(
    project_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """Make the calling superadmin an owner of any live project.

    Access comes from a real ProjectMember row rather than a permission bypass,
    so every existing check applies unchanged and the admin is listed in the
    project's People list. An existing live membership is upgraded in place, a
    soft-deleted one is revived as a fresh membership.
    """
    project = db.query(Project).filter(Project.id == project_id, Project.deleted_at.is_(None)).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    member = db.query(ProjectMember).filter(
        ProjectMember.project_id == project_id,
        ProjectMember.user_id == current_user.id,
    ).first()
    if member is None:
        db.add(ProjectMember(
            project_id=project_id, user_id=current_user.id,
            role=ProjectRole.owner, invited_by=current_user.id,
        ))
    elif member.deleted_at is not None:
        # Same revival as POST /projects/{id}/members: a new membership for the
        # member list's joined date and inviter.
        member.deleted_at = None
        member.role = ProjectRole.owner
        member.invited_by = current_user.id
        member.invited_at = func.now()
    else:
        member.role = ProjectRole.owner
    db.commit()
    db.refresh(project)

    return build_project_list(db, [project], {project.id: ProjectRole.owner}, include_owners=True)[0]


# ── Sign in with Discord ───────────────────────────────────────────────────────

# Fixed sentinel PK: instance_auth_settings is a singleton, so concurrent
# first-time creates collide on it instead of making two rows.
_AUTH_SETTINGS_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


def _discord_settings_response(row: InstanceAuthSettings | None) -> DiscordAuthSettingsResponse:
    config = discord_oauth.resolve_discord_config(row)
    return DiscordAuthSettingsResponse(
        enabled=config.enabled,
        client_id=config.client_id,
        client_secret_set=bool(config.client_secret),
        server_id=config.server_id,
        allowed_role_ids=config.allowed_role_ids,
        redirect_uri=discord_oauth.discord_redirect_uri(),
        configured_via=config.configured_via,
    )


def _get_or_create_auth_settings(db: Session) -> InstanceAuthSettings:
    row = discord_oauth.get_auth_settings_row(db)
    if row:
        return row
    # A new row starts from the env toggle, so saving only a client id does
    # not silently switch off a DISCORD_LOGIN_ENABLED=true instance.
    row = InstanceAuthSettings(id=_AUTH_SETTINGS_ID, discord_enabled=settings.discord_login_enabled)
    db.add(row)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        return discord_oauth.get_auth_settings_row(db)
    return row


@router.get("/auth/discord", response_model=DiscordAuthSettingsResponse)
def get_discord_auth_settings(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """Superadmin only. The client secret is never returned, only whether one is set."""
    return _discord_settings_response(discord_oauth.get_auth_settings_row(db))


@router.put("/auth/discord", response_model=DiscordAuthSettingsResponse)
def update_discord_auth_settings(
    body: DiscordAuthSettingsUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """Superadmin only. Omitted fields are unchanged; an empty or null value
    clears the saved value (its DISCORD_* env var then applies again)."""
    row = _get_or_create_auth_settings(db)
    fields = body.model_fields_set
    if "enabled" in fields and body.enabled is not None:
        row.discord_enabled = body.enabled
    if "client_id" in fields:
        row.discord_client_id = (body.client_id or "").strip() or None
    if "client_secret" in fields:
        secret = (body.client_secret or "").strip()
        row.discord_client_secret_encrypted = encrypt_secret(secret) if secret else None
    if "server_id" in fields:
        row.discord_server_id = body.server_id
    if "allowed_role_ids" in fields:
        row.discord_allowed_role_ids = body.allowed_role_ids or None

    config = discord_oauth.resolve_discord_config(row)
    problem = None
    if config.enabled and not (config.client_id and config.client_secret):
        problem = "Discord sign-in needs a client ID and a client secret before it can be enabled."
    elif config.allowed_role_ids and not config.server_id:
        problem = "Allowed roles need a Discord server ID."
    if problem:
        db.rollback()
        raise HTTPException(status_code=422, detail=problem)
    db.commit()
    db.refresh(row)
    return _discord_settings_response(row)
