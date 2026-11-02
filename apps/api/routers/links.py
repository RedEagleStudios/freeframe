"""Links in a project's folder tree: a name and an outside URL (an Outline doc,
say) that the web app opens in a new tab."""

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from ..database import get_db
from ..middleware.auth import get_current_user
from ..models.folder import Folder
from ..models.link import ProjectLink
from ..models.project import ProjectRole
from ..models.user import User
from ..schemas.link import ProjectLinkCreate, ProjectLinkResponse, ProjectLinkUpdate
from ..services.permissions import (
    folder_chain_is_live,
    require_project_role,
    require_project_visible,
)

router = APIRouter(tags=["links"])


def _require_live_folder(db: Session, project_id: uuid.UUID, folder_id: Optional[uuid.UUID]) -> None:
    """A link goes at the root or in a live folder of its own project."""
    if folder_id is None:
        return
    folder = (
        db.query(Folder.id)
        .filter(Folder.id == folder_id, Folder.project_id == project_id, Folder.deleted_at.is_(None))
        .first()
    )
    if folder is None or not folder_chain_is_live(db, folder_id):
        raise HTTPException(status_code=400, detail="Folder not found in this project")


def _get_link(db: Session, link_id: uuid.UUID) -> ProjectLink:
    link = db.query(ProjectLink).filter(ProjectLink.id == link_id).first()
    if link is None:
        raise HTTPException(status_code=404, detail="Link not found")
    return link


@router.get("/projects/{project_id}/links", response_model=list[ProjectLinkResponse])
def list_links(
    project_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Every link at the root or in a live folder. A trashed folder's links are
    left out with it and come back when it is restored."""
    require_project_visible(db, project_id, current_user)
    return (
        db.query(ProjectLink)
        .outerjoin(Folder, ProjectLink.folder_id == Folder.id)
        .filter(
            ProjectLink.project_id == project_id,
            or_(ProjectLink.folder_id.is_(None), Folder.deleted_at.is_(None)),
        )
        .order_by(func.lower(ProjectLink.name), ProjectLink.created_at)
        .all()
    )


@router.post(
    "/projects/{project_id}/links",
    response_model=ProjectLinkResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_link(
    project_id: uuid.UUID,
    body: ProjectLinkCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_project_role(db, project_id, current_user, ProjectRole.editor)
    _require_live_folder(db, project_id, body.folder_id)
    link = ProjectLink(
        project_id=project_id,
        folder_id=body.folder_id,
        name=body.name,
        url=body.url,
        created_by=current_user.id,
    )
    db.add(link)
    db.commit()
    db.refresh(link)
    return link


@router.patch("/links/{link_id}", response_model=ProjectLinkResponse)
def update_link(
    link_id: uuid.UUID,
    body: ProjectLinkUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    link = _get_link(db, link_id)
    require_project_role(db, link.project_id, current_user, ProjectRole.editor)
    if body.name is not None:
        link.name = body.name
    if body.url is not None:
        link.url = body.url
    if "folder_id" in body.model_fields_set:
        _require_live_folder(db, link.project_id, body.folder_id)
        link.folder_id = body.folder_id
    db.commit()
    db.refresh(link)
    return link


@router.delete("/links/{link_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_link(
    link_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Removes the link for good: it holds no files, only a name and an address."""
    link = _get_link(db, link_id)
    require_project_role(db, link.project_id, current_user, ProjectRole.editor)
    db.delete(link)
    db.commit()
