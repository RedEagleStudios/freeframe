"""Links in the folder tree (an Outline doc, say): an outside URL the web app
opens in a new tab. They follow their folder into and out of the trash, are
copied with a duplicated folder, and go with a purged folder."""

import uuid

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy.orm import Session

import apps.api.routers.folders as folders_module
import apps.api.routers.links as links_module
import apps.api.tasks.cleanup_tasks as ct
from apps.api.models.link import ProjectLink
from apps.api.models.project import Project, ProjectMember, ProjectRole, ProjectType
from apps.api.models.user import User
from apps.api.schemas.folder import FolderCreate
from apps.api.schemas.link import ProjectLinkCreate, ProjectLinkUpdate

OUTLINE = "https://outline.example.com/doc/script-ep1-abc123"


@pytest.fixture
def db():
    """A real session where each route commit is a SAVEPOINT the test can observe."""
    from apps.api.database import engine

    conn = engine.connect()
    trans = conn.begin()
    session = Session(bind=conn, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()
        trans.rollback()
        conn.close()


def _project(db, role=ProjectRole.editor):
    user = User(email=f"links-{uuid.uuid4()}@t.local", name="t")
    db.add(user)
    db.flush()
    project = Project(name=f"links-{uuid.uuid4()}", project_type=ProjectType.personal, created_by=user.id)
    db.add(project)
    db.flush()
    db.add(ProjectMember(project_id=project.id, user_id=user.id, role=role))
    db.commit()
    return user, project


@pytest.fixture
def project(db):
    return _project(db)


def _folder(db, owner, project, name, parent_id=None):
    return folders_module.create_folder(
        project.id, FolderCreate(name=name, parent_id=parent_id), db=db, current_user=owner,
    ).id


def _link(db, owner, project, name, folder_id=None, url=OUTLINE):
    return links_module.create_link(
        project.id, ProjectLinkCreate(name=name, url=url, folder_id=folder_id), db=db, current_user=owner,
    )


def _listed(db, owner, project):
    return [(l.name, l.folder_id) for l in links_module.list_links(project.id, db=db, current_user=owner)]


def test_links_sit_at_the_root_or_in_a_folder(db, project):
    owner, project = project
    episode = _folder(db, owner, project, "000")

    _link(db, owner, project, "Script", episode)
    _link(db, owner, project, "Bible")

    assert _listed(db, owner, project) == [("Bible", None), ("Script", episode)]


def test_a_link_counts_as_one_of_its_folders_items(db, project):
    owner, project = project
    episode = _folder(db, owner, project, "000")
    _folder(db, owner, project, "Assets", episode)
    _link(db, owner, project, "Script", episode)

    [node] = folders_module.get_folder_tree(project.id, db=db, current_user=owner)
    [listed] = folders_module.list_folders(project.id, parent_id="root", db=db, current_user=owner)

    assert node.item_count == 2
    assert listed.item_count == 2



@pytest.mark.parametrize("url", [
    "javascript:alert(1)",
    "data:text/html,<script>alert(1)</script>",
    "outline.example.com/doc/x",
    "https://",
])
def test_only_full_web_addresses_are_accepted(url):
    with pytest.raises(ValidationError):
        ProjectLinkCreate(name="Script", url=url)
    with pytest.raises(ValidationError):
        ProjectLinkUpdate(url=url)


def test_a_trashed_folder_hides_its_links_until_it_is_restored(db, project):
    owner, project = project
    episode = _folder(db, owner, project, "000")
    inner = _folder(db, owner, project, "Assets", episode)
    _link(db, owner, project, "Script", inner)

    folders_module.delete_folder(episode, db=db, current_user=owner)
    assert _listed(db, owner, project) == []

    folders_module.restore_folder(episode, db=db, current_user=owner)
    assert _listed(db, owner, project) == [("Script", inner)]


def test_a_link_cannot_go_into_another_project_or_the_trash(db, project):
    owner, project = project
    other_owner, other = _project(db)
    foreign = _folder(db, other_owner, other, "Theirs")
    trashed = _folder(db, owner, project, "Old")
    folders_module.delete_folder(trashed, db=db, current_user=owner)

    for folder_id in (foreign, trashed):
        with pytest.raises(HTTPException) as exc_info:
            _link(db, owner, project, "Script", folder_id)
        assert exc_info.value.status_code == 400

    link = _link(db, owner, project, "Script")
    with pytest.raises(HTTPException) as exc_info:
        links_module.update_link(link.id, ProjectLinkUpdate(folder_id=foreign), db=db, current_user=owner)
    assert exc_info.value.status_code == 400


def test_reviewers_see_links_but_cannot_change_them(db, project):
    owner, project = project
    reviewer = User(email=f"links-rev-{uuid.uuid4()}@t.local", name="r")
    db.add(reviewer)
    db.flush()
    db.add(ProjectMember(project_id=project.id, user_id=reviewer.id, role=ProjectRole.reviewer))
    db.commit()
    link = _link(db, owner, project, "Script")

    assert _listed(db, reviewer, project) == [("Script", None)]
    for attempt in (
        lambda: _link(db, reviewer, project, "Mine"),
        lambda: links_module.update_link(link.id, ProjectLinkUpdate(name="X"), db=db, current_user=reviewer),
        lambda: links_module.delete_link(link.id, db=db, current_user=reviewer),
    ):
        with pytest.raises(HTTPException) as exc_info:
            attempt()
        assert exc_info.value.status_code == 403


def test_editing_moves_renames_and_repoints_a_link(db, project):
    owner, project = project
    episode = _folder(db, owner, project, "000")
    link = _link(db, owner, project, "Script", episode)

    updated = links_module.update_link(
        link.id,
        ProjectLinkUpdate(name="Script v2", url="https://outline.example.com/doc/v2", folder_id=None),
        db=db, current_user=owner,
    )

    assert (updated.name, updated.url, updated.folder_id) == ("Script v2", "https://outline.example.com/doc/v2", None)


def test_duplicating_a_folder_copies_its_links(db, project):
    owner, project = project
    episode = _folder(db, owner, project, "000")
    assets = _folder(db, owner, project, "Assets", episode)
    _link(db, owner, project, "Script", episode)
    _link(db, owner, project, "Shot list", assets)

    copy = folders_module.duplicate_folder(episode, db=db, current_user=owner).folder

    listed = _listed(db, owner, project)
    copied = {name: folder_id for name, folder_id in listed if folder_id not in (episode, assets)}
    assert copied["Script"] == copy.id
    assert len(listed) == 4


def test_purging_a_folder_removes_its_links(db, project, monkeypatch):
    monkeypatch.setattr(ct, "delete_object", lambda k: None)
    monkeypatch.setattr(ct, "delete_prefix", lambda k: None)
    owner, project = project
    episode = _folder(db, owner, project, "000")
    _link(db, owner, project, "Script", episode)
    _link(db, owner, project, "Bible")

    ct._purge_folder(db, episode, ct.PurgeCounts())

    assert [l.name for l in db.query(ProjectLink).filter(ProjectLink.project_id == project.id)] == ["Bible"]
