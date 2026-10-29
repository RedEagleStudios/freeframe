"""Single-use project join links.

Owners create, list and revoke them under /projects/{id}/join-links. Anyone
may preview one (`GET /project-join-links/{token}`); a signed-in user redeems
it with `POST /project-join-links/{token}/accept`, and a signed-out one through
Sign in with Discord (`/auth/discord/start?join=<token>`).
"""
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import or_
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db
from ..middleware.auth import get_current_user
from ..middleware.rate_limit import rate_limit
from ..models.project import ProjectJoinLink
from ..models.user import User
from ..schemas.project_join_link import (
    ProjectJoinLinkAccepted, ProjectJoinLinkCreate, ProjectJoinLinkCreated,
    ProjectJoinLinkPreview, ProjectJoinLinkResponse,
)
from ..services import project_join_links as join_links
from .projects import _get_project, _require_project_owner

router = APIRouter(tags=["project-join-links"])

# Spent, expired and revoked links stay in the owner's list this long.
LIST_RECENT_DAYS = 30


def _status(link: ProjectJoinLink, now: datetime) -> str:
    if link.revoked_at is not None:
        return "revoked"
    if link.used_at is not None:
        return "used"
    if link.expires_at is not None and link.expires_at <= now:
        return "expired"
    return "active"


def _names(db: Session, ids: set) -> dict:
    ids = {i for i in ids if i is not None}
    if not ids:
        return {}
    return dict(db.query(User.id, User.name).filter(User.id.in_(ids)).all())


def _response(link: ProjectJoinLink, names: dict, now: datetime) -> ProjectJoinLinkResponse:
    return ProjectJoinLinkResponse(
        id=link.id,
        project_id=link.project_id,
        role=link.role,
        status=_status(link, now),
        created_at=link.created_at,
        created_by=link.created_by,
        created_by_name=names.get(link.created_by),
        expires_at=link.expires_at,
        used_at=link.used_at,
        used_by=link.used_by,
        used_by_name=names.get(link.used_by),
        revoked_at=link.revoked_at,
    )


@router.post(
    "/projects/{project_id}/join-links",
    response_model=ProjectJoinLinkCreated,
    status_code=status.HTTP_201_CREATED,
)
def create_join_link(
    project_id: uuid.UUID,
    body: ProjectJoinLinkCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _get_project(db, project_id)
    _require_project_owner(db, project_id, current_user)
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(days=body.expires_in_days) if body.expires_in_days else None
    link, token = join_links.new_link(
        db, project_id=project_id, role=body.role, created_by=current_user.id, expires_at=expires_at,
    )
    db.commit()
    db.refresh(link)
    base = _response(link, {current_user.id: current_user.name}, now)
    return ProjectJoinLinkCreated(
        **base.model_dump(), token=token, url=join_links.join_url(settings.frontend_url, token),
    )


@router.get("/projects/{project_id}/join-links", response_model=list[ProjectJoinLinkResponse])
def list_join_links(
    project_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _get_project(db, project_id)
    _require_project_owner(db, project_id, current_user)
    now = datetime.now(timezone.utc)
    recent = now - timedelta(days=LIST_RECENT_DAYS)
    links = (
        db.query(ProjectJoinLink)
        .filter(
            ProjectJoinLink.project_id == project_id,
            or_(ProjectJoinLink.revoked_at.is_(None), ProjectJoinLink.revoked_at > recent),
            or_(ProjectJoinLink.used_at.is_(None), ProjectJoinLink.used_at > recent),
            or_(ProjectJoinLink.expires_at.is_(None), ProjectJoinLink.expires_at > recent),
        )
        .order_by(ProjectJoinLink.created_at.desc())
        .all()
    )
    names = _names(db, {l.created_by for l in links} | {l.used_by for l in links})
    return [_response(link, names, now) for link in links]


@router.delete("/projects/{project_id}/join-links/{link_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_join_link(
    project_id: uuid.UUID,
    link_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _get_project(db, project_id)
    _require_project_owner(db, project_id, current_user)
    link = db.query(ProjectJoinLink).filter(
        ProjectJoinLink.id == link_id, ProjectJoinLink.project_id == project_id,
    ).first()
    if link is None:
        raise HTTPException(status_code=404, detail="Join link not found")
    if link.revoked_at is None:
        link.revoked_at = datetime.now(timezone.utc)
        db.commit()


@router.get(
    "/project-join-links/{token}",
    response_model=ProjectJoinLinkPreview,
    dependencies=[Depends(rate_limit("join_link_preview", 60, 600))],
)
def preview_join_link(token: str, db: Session = Depends(get_db)):
    valid, _, reason = join_links.find_link(db, token)
    if valid is None:
        return ProjectJoinLinkPreview(valid=False, reason=reason or "not_found")
    return ProjectJoinLinkPreview(valid=True, project_name=valid.project.name, role=valid.link.role)


@router.post(
    "/project-join-links/{token}/accept",
    response_model=ProjectJoinLinkAccepted,
    dependencies=[Depends(rate_limit("join_link_accept", 20, 600))],
)
def accept_join_link(
    token: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        valid = join_links.validate(db, token)
        member = join_links.redeem(db, valid, current_user)
    except join_links.JoinLinkInvalid as exc:
        raise HTTPException(
            status_code=404 if exc.reason == "not_found" else 410, detail=exc.reason,
        )
    return ProjectJoinLinkAccepted(project_id=member.project_id, role=member.role)
