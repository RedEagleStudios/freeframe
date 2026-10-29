"""Superadmin access to every project.

A private project's members are managed only by its owners, so one whose owners
are gone is unreachable. `GET /projects?scope=all` lets a superadmin see every
live project and `POST /admin/projects/{id}/join` makes them an owner through a
real membership row, so the usual permission checks apply and the team sees them
in People. These pin both endpoints against the queries they depend on.
"""
import uuid
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException


def _user(real_db, tag, *, superadmin=False):
    from apps.api.models.user import User
    u = User(email=f"apa-{tag}-{uuid.uuid4()}@t.local", name=f"apa {tag}", is_superadmin=superadmin)
    real_db.add(u); real_db.flush()
    return u


def _project(real_db, owner, *, public=False, deleted=False):
    from apps.api.models.project import Project, ProjectMember, ProjectRole, ProjectType
    p = Project(name=f"apa-{uuid.uuid4()}", project_type=ProjectType.personal,
                created_by=owner.id, is_public=public,
                deleted_at=datetime.now(timezone.utc) if deleted else None)
    real_db.add(p); real_db.flush()
    real_db.add(ProjectMember(project_id=p.id, user_id=owner.id, role=ProjectRole.owner))
    real_db.flush()
    return p


def _memberships(real_db, project, user):
    from apps.api.models.project import ProjectMember
    return real_db.query(ProjectMember).filter(
        ProjectMember.project_id == project.id, ProjectMember.user_id == user.id,
    ).all()


# ── Refusals (mock DB) ──────────────────────────────────────────────────────

def test_scope_all_is_refused_to_non_superadmins(client, auth_headers):
    resp = client.get("/projects?scope=all", headers=auth_headers)
    assert resp.status_code == 403


def test_join_is_refused_to_non_superadmins(client, auth_headers, mock_db):
    resp = client.post(f"/admin/projects/{uuid.uuid4()}/join", headers=auth_headers)
    assert resp.status_code == 403
    mock_db.commit.assert_not_called()


# ── Listing (real DB) ───────────────────────────────────────────────────────

def test_scope_all_lists_every_live_project_with_owners(real_db):
    from apps.api.routers.projects import list_projects

    admin = _user(real_db, "admin", superadmin=True)
    alice = _user(real_db, "alice")
    private = _project(real_db, alice)
    gone = _project(real_db, alice, deleted=True)

    rows = {r.id: r for r in list_projects(scope="all", db=real_db, current_user=admin)}

    assert private.id in rows
    assert gone.id not in rows
    row = rows[private.id]
    assert row.role is None
    assert [(o.id, o.name, o.email) for o in row.owners] == [(alice.id, alice.name, alice.email)]


def test_scope_all_owners_skip_removed_and_non_owner_members(real_db):
    from apps.api.models.project import ProjectMember, ProjectRole
    from apps.api.routers.projects import list_projects

    admin = _user(real_db, "admin", superadmin=True)
    alice, bob, carol = _user(real_db, "alice"), _user(real_db, "bob"), _user(real_db, "carol")
    p = _project(real_db, alice)
    real_db.add(ProjectMember(project_id=p.id, user_id=bob.id, role=ProjectRole.owner,
                              deleted_at=datetime.now(timezone.utc)))
    real_db.add(ProjectMember(project_id=p.id, user_id=carol.id, role=ProjectRole.editor))
    real_db.flush()

    row = next(r for r in list_projects(scope="all", db=real_db, current_user=admin) if r.id == p.id)
    assert [o.id for o in row.owners] == [alice.id]


def test_default_listing_for_superadmin_is_still_memberships_and_public(real_db):
    from apps.api.routers.projects import list_projects

    admin = _user(real_db, "admin", superadmin=True)
    alice = _user(real_db, "alice")
    private = _project(real_db, alice)
    public = _project(real_db, alice, public=True)
    own = _project(real_db, admin)

    ids = {r.id for r in list_projects(scope="mine", db=real_db, current_user=admin)}

    assert private.id not in ids
    assert {public.id, own.id} <= ids


def test_normal_listing_does_not_reveal_owner_emails_of_public_projects(real_db):
    from apps.api.routers.projects import list_projects

    alice, bob = _user(real_db, "alice"), _user(real_db, "bob")
    public = _project(real_db, alice, public=True)

    row = next(r for r in list_projects(scope="mine", db=real_db, current_user=bob) if r.id == public.id)

    assert row.owners == []


# ── Joining (real DB) ───────────────────────────────────────────────────────

def test_join_creates_an_owner_membership(real_db):
    from apps.api.models.project import ProjectRole
    from apps.api.routers.admin import join_project_as_owner

    admin, alice = _user(real_db, "admin", superadmin=True), _user(real_db, "alice")
    p = _project(real_db, alice)

    resp = join_project_as_owner(p.id, db=real_db, current_user=admin)

    assert resp.id == p.id and resp.role == ProjectRole.owner
    assert resp.member_count == 2
    rows = _memberships(real_db, p, admin)
    assert len(rows) == 1
    assert rows[0].role == ProjectRole.owner and rows[0].deleted_at is None


def test_join_upgrades_an_existing_membership_without_a_duplicate(real_db):
    from apps.api.models.project import ProjectMember, ProjectRole
    from apps.api.routers.admin import join_project_as_owner

    admin, alice = _user(real_db, "admin", superadmin=True), _user(real_db, "alice")
    p = _project(real_db, alice)
    real_db.add(ProjectMember(project_id=p.id, user_id=admin.id, role=ProjectRole.viewer))
    real_db.flush()

    join_project_as_owner(p.id, db=real_db, current_user=admin)

    rows = _memberships(real_db, p, admin)
    assert len(rows) == 1
    assert rows[0].role == ProjectRole.owner


def test_join_revives_a_removed_membership(real_db):
    from apps.api.models.project import ProjectMember, ProjectRole
    from apps.api.routers.admin import join_project_as_owner

    admin, alice = _user(real_db, "admin", superadmin=True), _user(real_db, "alice")
    p = _project(real_db, alice)
    removed = ProjectMember(project_id=p.id, user_id=admin.id, role=ProjectRole.viewer,
                            invited_at=datetime(2020, 1, 1, tzinfo=timezone.utc),
                            deleted_at=datetime.now(timezone.utc))
    real_db.add(removed); real_db.flush()

    join_project_as_owner(p.id, db=real_db, current_user=admin)

    rows = _memberships(real_db, p, admin)
    assert len(rows) == 1
    real_db.refresh(rows[0])
    assert rows[0].deleted_at is None
    assert rows[0].role == ProjectRole.owner
    assert rows[0].invited_by == admin.id
    assert rows[0].invited_at.year > 2020


def test_join_on_a_deleted_project_is_404(real_db):
    from apps.api.routers.admin import join_project_as_owner

    admin, alice = _user(real_db, "admin", superadmin=True), _user(real_db, "alice")
    p = _project(real_db, alice, deleted=True)

    with pytest.raises(HTTPException) as exc:
        join_project_as_owner(p.id, db=real_db, current_user=admin)
    assert exc.value.status_code == 404
    assert _memberships(real_db, p, admin) == []
