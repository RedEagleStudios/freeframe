"""Single-use project join links.

An owner creates a link; whoever opens it first is added to that one project
with the link's role, and the link is spent. Only a SHA-256 of the token is
stored, so the database never holds a usable link.

A link is spent by one conditional UPDATE (`... WHERE used_at IS NULL AND
revoked_at IS NULL AND not expired`), so two people opening it at once can
never both get in. Someone who is already a live member with an equal or
higher role does not spend it.
"""
import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal, Optional

from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models.project import Project, ProjectJoinLink, ProjectMember, ProjectRole
from ..models.user import User
from .project_members import add_or_revive_member

InvalidReason = Literal["expired", "revoked", "used_up", "not_found"]

# How strong each role is, for "keep the higher role".
ROLE_RANK = {
    ProjectRole.viewer: 0,
    ProjectRole.reviewer: 1,
    ProjectRole.editor: 2,
    ProjectRole.owner: 3,
}


class JoinLinkInvalid(Exception):
    def __init__(self, reason: InvalidReason):
        super().__init__(reason)
        self.reason: InvalidReason = reason


@dataclass(frozen=True)
class ValidLink:
    link: ProjectJoinLink
    project: Project


def generate_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _invalid_reason(link: ProjectJoinLink, now: datetime) -> Optional[InvalidReason]:
    if link.revoked_at is not None:
        return "revoked"
    if link.used_at is not None:
        return "used_up"
    if link.expires_at is not None and link.expires_at <= now:
        return "expired"
    return None


def find_link(db: Session, token: Optional[str]) -> tuple[Optional[ValidLink], Optional[Project], Optional[InvalidReason]]:
    """(valid link, its live project, reason it is invalid).

    The project is returned for an invalid link too, so a preview can still
    name it; a link whose project is gone is `not_found`.
    """
    if not token:
        return None, None, "not_found"
    link = db.query(ProjectJoinLink).filter(ProjectJoinLink.token_hash == hash_token(token)).first()
    if link is None:
        return None, None, "not_found"
    project = db.query(Project).filter(Project.id == link.project_id, Project.deleted_at.is_(None)).first()
    if project is None:
        return None, None, "not_found"
    reason = _invalid_reason(link, _now())
    if reason:
        return None, project, reason
    return ValidLink(link=link, project=project), project, None


def validate(db: Session, token: Optional[str]) -> ValidLink:
    valid, _, reason = find_link(db, token)
    if valid is None:
        raise JoinLinkInvalid(reason or "not_found")
    return valid


def redeem(db: Session, valid: ValidLink, user: User) -> ProjectMember:
    """Give `user` the link's membership, spending the link unless they already have it.

    Commits. Raises JoinLinkInvalid when the link was spent, revoked or expired
    since it was validated.
    """
    link = valid.link
    member = (
        db.query(ProjectMember)
        .filter(ProjectMember.project_id == link.project_id, ProjectMember.user_id == user.id)
        .with_for_update()
        .first()
    )
    if member is not None and member.deleted_at is None and ROLE_RANK[member.role] >= ROLE_RANK[link.role]:
        # Already in with this role or better: nothing to do, and the link
        # stays usable. The commit only ends the transaction holding the lock.
        db.commit()
        return member

    now = _now()
    claimed = (
        db.query(ProjectJoinLink)
        .filter(
            ProjectJoinLink.id == link.id,
            ProjectJoinLink.used_at.is_(None),
            ProjectJoinLink.revoked_at.is_(None),
            or_(ProjectJoinLink.expires_at.is_(None), ProjectJoinLink.expires_at > now),
        )
        .update(
            {ProjectJoinLink.used_at: now, ProjectJoinLink.used_by: user.id},
            synchronize_session=False,
        )
    )
    if claimed != 1:
        db.rollback()
        raise JoinLinkInvalid("used_up")

    if member is not None and member.deleted_at is None:
        # A live member with a lower role is raised to the link's.
        member.role = link.role
    else:
        member = add_or_revive_member(
            db, project_id=link.project_id, user_id=user.id, role=link.role,
            invited_by=link.created_by, existing=member,
        )
    try:
        db.commit()
    except IntegrityError:
        # A concurrent join by the same user created the membership first;
        # the rollback also gives the link back.
        db.rollback()
        raise JoinLinkInvalid("used_up")
    db.refresh(member)
    return member


def join_url(frontend_url: str, token: str) -> str:
    return f"{frontend_url.rstrip('/')}/join/{token}"


def new_link(
    db: Session, *, project_id: uuid.UUID, role: ProjectRole, created_by: uuid.UUID,
    expires_at: Optional[datetime],
) -> tuple[ProjectJoinLink, str]:
    """A new link row (added, not committed) and its raw token."""
    token = generate_token()
    link = ProjectJoinLink(
        id=uuid.uuid4(),
        project_id=project_id,
        token_hash=hash_token(token),
        role=role,
        created_by=created_by,
        expires_at=expires_at,
    )
    db.add(link)
    return link, token
