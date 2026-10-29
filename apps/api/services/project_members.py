"""Adding someone to a project, shared by the members API and join links."""
import uuid
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from ..models.project import ProjectMember, ProjectRole


def add_or_revive_member(
    db: Session, *, project_id: uuid.UUID, user_id: uuid.UUID, role: ProjectRole,
    invited_by: Optional[uuid.UUID], existing: Optional[ProjectMember],
) -> ProjectMember:
    """A live membership for `user_id`, reviving `existing` (their soft-deleted row) when given.

    The caller has checked `existing` is not live and commits. A revived row is
    a new membership as far as the member list's joined date and inviter go.
    """
    if existing is not None:
        existing.deleted_at = None
        existing.role = role
        existing.invited_by = invited_by
        existing.invited_at = func.now()
        return existing
    member = ProjectMember(project_id=project_id, user_id=user_id, role=role, invited_by=invited_by)
    db.add(member)
    return member
