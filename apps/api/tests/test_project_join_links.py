"""Project join links: the owner endpoints, the public preview and accept.

Mock DB: the link lookup (`find_link`) is patched with one that applies the real
validity rules to a link the test controls. The race and the stored rows are
covered against real Postgres in test_project_join_links_db.py.
"""
import hashlib
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from apps.api.models.project import Project, ProjectJoinLink, ProjectMember, ProjectRole
from apps.api.services import project_join_links as join_links

TOKEN = "tok-123"


@pytest.fixture(autouse=True)
def _no_rate_limit():
    with patch("apps.api.middleware.rate_limit.check_rate_limit", return_value=(True, 0)):
        yield


def _owner_lookups(mock_db, role=ProjectRole.owner):
    """`_get_project` and `_require_project_owner` both read `.first()`."""
    row = MagicMock()
    row.role = role
    row.deleted_at = None
    mock_db.first.return_value = row
    return row


# ── create / list / revoke ────────────────────────────────────────────────────

def test_create_returns_url_once_and_stores_only_the_hash(client, auth_headers, mock_db, test_user):
    _owner_lookups(mock_db)
    project_id = uuid.uuid4()
    resp = client.post(f"/projects/{project_id}/join-links", headers=auth_headers, json={})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    token = body["token"]
    assert body["url"] == f"http://localhost:3000/join/{token}"
    assert body["role"] == "reviewer"
    assert body["status"] == "active"
    assert body["created_by_name"] == test_user.name
    expires = datetime.fromisoformat(body["expires_at"])
    assert abs(expires - (datetime.now(timezone.utc) + timedelta(days=7))) < timedelta(minutes=1)

    [link] = [c.args[0] for c in mock_db.add.call_args_list if isinstance(c.args[0], ProjectJoinLink)]
    assert link.token_hash == hashlib.sha256(token.encode()).hexdigest()
    assert link.project_id == project_id and link.created_by == test_user.id
    assert token not in [str(v) for v in vars(link).values()]
    assert link.used_at is None and link.used_by is None


def test_create_never_expiring_editor_link(client, auth_headers, mock_db):
    _owner_lookups(mock_db)
    resp = client.post(f"/projects/{uuid.uuid4()}/join-links", headers=auth_headers,
                       json={"role": "editor", "expires_in_days": None})
    assert resp.status_code == 201
    assert resp.json()["role"] == "editor" and resp.json()["expires_at"] is None


@pytest.mark.parametrize("body", [{"role": "owner"}, {"expires_in_days": 3}, {"role": "admin"}])
def test_create_rejects_owner_role_and_odd_expiry(client, auth_headers, mock_db, body):
    _owner_lookups(mock_db)
    resp = client.post(f"/projects/{uuid.uuid4()}/join-links", headers=auth_headers, json=body)
    assert resp.status_code == 422
    mock_db.add.assert_not_called()


@pytest.mark.parametrize("role", [ProjectRole.editor, ProjectRole.reviewer, ProjectRole.viewer])
@pytest.mark.parametrize("call", [
    lambda c, pid, h: c.post(f"/projects/{pid}/join-links", headers=h, json={}),
    lambda c, pid, h: c.get(f"/projects/{pid}/join-links", headers=h),
    lambda c, pid, h: c.delete(f"/projects/{pid}/join-links/{uuid.uuid4()}", headers=h),
], ids=["create", "list", "revoke"])
def test_non_owner_cannot_manage_links(client, auth_headers, mock_db, role, call):
    _owner_lookups(mock_db, role=role)
    assert call(client, uuid.uuid4(), auth_headers).status_code == 403
    mock_db.add.assert_not_called()
    mock_db.commit.assert_not_called()


def test_non_member_cannot_manage_links(client, auth_headers, mock_db):
    mock_db.first.side_effect = [MagicMock(), None]  # project exists, no membership
    resp = client.get(f"/projects/{uuid.uuid4()}/join-links", headers=auth_headers)
    assert resp.status_code == 403


def test_list_shows_status_and_who_used_it(client, auth_headers, mock_db, test_user):
    _owner_lookups(mock_db)
    now = datetime.now(timezone.utc)
    pid, used_by = uuid.uuid4(), uuid.uuid4()

    def link(**over):
        base = dict(id=uuid.uuid4(), project_id=pid, token_hash="h" * 64, role=ProjectRole.reviewer,
                    created_by=test_user.id, created_at=now)
        base.update(over)
        return ProjectJoinLink(**base)

    mock_db.order_by.return_value = mock_db
    mock_db.all.side_effect = [
        [link(), link(used_at=now, used_by=used_by), link(expires_at=now - timedelta(days=1)),
         link(revoked_at=now)],
        [(test_user.id, "Owner"), (used_by, "Guest Person")],
    ]
    resp = client.get(f"/projects/{pid}/join-links", headers=auth_headers)
    assert resp.status_code == 200
    rows = resp.json()
    assert [r["status"] for r in rows] == ["active", "used", "expired", "revoked"]
    assert rows[1]["used_by_name"] == "Guest Person" and rows[1]["used_at"] is not None
    assert all(r["created_by_name"] == "Owner" for r in rows)
    assert all("token" not in r and "token_hash" not in r for r in rows)


def test_revoke_sets_revoked_at(client, auth_headers, mock_db):
    owner = MagicMock(role=ProjectRole.owner)
    link = ProjectJoinLink(id=uuid.uuid4(), project_id=uuid.uuid4(), role=ProjectRole.viewer)
    mock_db.first.side_effect = [MagicMock(), owner, link]
    resp = client.delete(f"/projects/{link.project_id}/join-links/{link.id}", headers=auth_headers)
    assert resp.status_code == 204
    assert link.revoked_at is not None
    mock_db.commit.assert_called_once()


def test_revoke_unknown_link_404(client, auth_headers, mock_db):
    mock_db.first.side_effect = [MagicMock(), MagicMock(role=ProjectRole.owner), None]
    resp = client.delete(f"/projects/{uuid.uuid4()}/join-links/{uuid.uuid4()}", headers=auth_headers)
    assert resp.status_code == 404


# ── preview / accept ──────────────────────────────────────────────────────────

@pytest.fixture
def link(mock_db):
    creator = uuid.uuid4()
    project = Project(id=uuid.uuid4(), name="Launch film", created_by=creator, description="secret notes")
    link = ProjectJoinLink(
        id=uuid.uuid4(), project_id=project.id, token_hash=join_links.hash_token(TOKEN),
        role=ProjectRole.reviewer, created_by=creator,
    )

    def fake_find(db, token):
        if token != TOKEN:
            return None, None, "not_found"
        reason = join_links._invalid_reason(link, datetime.now(timezone.utc))
        if reason:
            return None, project, reason
        return join_links.ValidLink(link=link, project=project), project, None

    mock_db.with_for_update.return_value = mock_db
    mock_db.update.return_value = 1
    with patch("apps.api.services.project_join_links.find_link", side_effect=fake_find):
        yield link


def test_preview_valid_reveals_only_name_and_role(client, link):
    resp = client.get(f"/project-join-links/{TOKEN}")
    assert resp.status_code == 200
    assert resp.json() == {"valid": True, "project_name": "Launch film", "role": "reviewer", "reason": None}
    assert "secret notes" not in resp.text and str(link.project_id) not in resp.text


@pytest.mark.parametrize("spoil, reason", [
    ("revoked_at", "revoked"), ("used_at", "used_up"), ("expires_at", "expired"), (None, "not_found"),
])
def test_preview_invalid_gives_reason_only(client, link, spoil, reason):
    token = TOKEN
    if spoil:
        setattr(link, spoil, datetime.now(timezone.utc) - timedelta(seconds=1))
    else:
        token = "nope"
    resp = client.get(f"/project-join-links/{token}")
    assert resp.json() == {"valid": False, "project_name": None, "role": None, "reason": reason}


def test_accept_adds_signed_in_user_and_spends_link(client, auth_headers, mock_db, test_user, link):
    resp = client.post(f"/project-join-links/{TOKEN}/accept", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"project_id": str(link.project_id), "role": "reviewer"}
    [member] = [c.args[0] for c in mock_db.add.call_args_list if isinstance(c.args[0], ProjectMember)]
    assert member.user_id == test_user.id and member.role == ProjectRole.reviewer
    values = mock_db.update.call_args.args[0]
    assert values[ProjectJoinLink.used_by] == test_user.id


def test_accept_revives_soft_deleted_membership(client, auth_headers, mock_db, test_user, link):
    old = ProjectMember(project_id=link.project_id, user_id=test_user.id, role=ProjectRole.owner,
                        deleted_at=datetime.now(timezone.utc))
    mock_db.first.return_value = old
    resp = client.post(f"/project-join-links/{TOKEN}/accept", headers=auth_headers)
    assert resp.status_code == 200
    assert old.deleted_at is None and old.role == ProjectRole.reviewer
    assert old.invited_by == link.created_by
    mock_db.add.assert_not_called()


def test_accept_raises_a_lower_role(client, auth_headers, mock_db, test_user, link):
    viewer = ProjectMember(project_id=link.project_id, user_id=test_user.id, role=ProjectRole.viewer)
    mock_db.first.return_value = viewer
    resp = client.post(f"/project-join-links/{TOKEN}/accept", headers=auth_headers)
    assert resp.json()["role"] == "reviewer"
    mock_db.update.assert_called_once()


@pytest.mark.parametrize("role", [ProjectRole.reviewer, ProjectRole.editor, ProjectRole.owner])
def test_accept_by_member_with_equal_or_higher_role_keeps_it_and_link(client, auth_headers, mock_db, test_user, link, role):
    member = ProjectMember(project_id=link.project_id, user_id=test_user.id, role=role)
    mock_db.first.return_value = member
    resp = client.post(f"/project-join-links/{TOKEN}/accept", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["role"] == role.value
    assert member.role == role
    mock_db.update.assert_not_called()


def test_accept_second_person_refused(client, auth_headers, mock_db, link):
    link.used_at, link.used_by = datetime.now(timezone.utc), uuid.uuid4()
    resp = client.post(f"/project-join-links/{TOKEN}/accept", headers=auth_headers)
    assert resp.status_code == 410 and resp.json()["detail"] == "used_up"
    mock_db.add.assert_not_called()


def test_accept_loses_the_race_refused(client, auth_headers, mock_db, link):
    mock_db.update.return_value = 0
    resp = client.post(f"/project-join-links/{TOKEN}/accept", headers=auth_headers)
    assert resp.status_code == 410
    mock_db.rollback.assert_called()
    mock_db.commit.assert_not_called()


def test_accept_unknown_404_and_needs_sign_in(client, auth_headers, link):
    assert client.post("/project-join-links/nope/accept", headers=auth_headers).status_code == 404


def test_accept_requires_authentication(client, link):
    assert client.post(f"/project-join-links/{TOKEN}/accept").status_code in (401, 403)
