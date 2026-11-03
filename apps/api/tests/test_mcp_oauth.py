"""MCP server sign-in (OAuth 2.1) and tools, end to end against real Postgres.

Drives the MCP app's real protocol endpoints (/register, /authorize, /token,
/mcp) the way an AI client does. Redis holds only the parked consent request
and the one-time code; a dict stands in for it (CI has no Redis service).
"""
import base64
import hashlib
import os
import secrets
import shutil
import subprocess
import uuid
from contextlib import nullcontext
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient
from mcp.shared.auth import OAuthClientInformationFull

from apps.api.config import settings
from apps.api.services import oauth_service, redis_service
from apps.api.services.mcp_server import McpEndpoint, mcp_disabled_reason

REDIRECT = "http://localhost:33418/callback"
MCP_HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


class _DictRedis:
    def __init__(self):
        self.data = {}

    def setex(self, key, ttl, value):
        self.data[key] = value

    def get(self, key):
        return self.data.get(key)

    def getdel(self, key):
        return self.data.pop(key, None)


@pytest.fixture
def fake_redis(monkeypatch):
    r = _DictRedis()
    monkeypatch.setattr(redis_service, "get_redis", lambda: r)
    return r


@pytest.fixture
def mcp(real_db, fake_redis):
    endpoint = McpEndpoint(session_scope=lambda: nullcontext(real_db))
    with TestClient(endpoint.app) as c:
        yield c


def _seed(real_db):
    from apps.api.models.asset import Asset, AssetType, AssetVersion, ProcessingStatus
    from apps.api.models.project import Project, ProjectMember, ProjectRole, ProjectType
    from apps.api.models.user import User

    me = User(email=f"mcp-me-{uuid.uuid4()}@t.local", name="Me", token_version=1)
    other = User(email=f"mcp-other-{uuid.uuid4()}@t.local", name="Other", token_version=1)
    real_db.add_all([me, other]); real_db.flush()
    mine = Project(name=f"mine-{uuid.uuid4()}", project_type=ProjectType.team, created_by=me.id)
    theirs = Project(name=f"theirs-{uuid.uuid4()}", project_type=ProjectType.team, created_by=other.id)
    real_db.add_all([mine, theirs]); real_db.flush()
    real_db.add_all([
        ProjectMember(project_id=mine.id, user_id=me.id, role=ProjectRole.owner),
        ProjectMember(project_id=theirs.id, user_id=other.id, role=ProjectRole.owner),
    ])
    asset = Asset(project_id=mine.id, name="clip", asset_type=AssetType.video, created_by=me.id)
    real_db.add(asset); real_db.flush()
    real_db.add(AssetVersion(asset_id=asset.id, version_number=1, processing_status=ProcessingStatus.ready, created_by=me.id))
    real_db.flush()
    return me, other, mine, theirs, asset


def _register(c):
    r = c.post("/register", json={
        "client_name": "Test Agent", "redirect_uris": [REDIRECT],
        "grant_types": ["authorization_code", "refresh_token"], "response_types": ["code"],
        "token_endpoint_auth_method": "none",
    })
    assert r.status_code == 201, r.text
    return r.json()["client_id"]


def _authorize(c, client_id, *, resource=None):
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    r = c.get("/authorize", params={
        "client_id": client_id, "response_type": "code", "redirect_uri": REDIRECT, "state": "st-1",
        "code_challenge": challenge, "code_challenge_method": "S256",
        "resource": resource or settings.mcp_url,
    }, follow_redirects=False)
    assert r.status_code == 302, r.text
    return r.headers["location"], verifier


def _query(url):
    return {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}


def _connect(c, user, *, allow_write=True):
    """Register, authorize, approve as `user`, and redeem the code: what an AI app does."""
    client_id = _register(c)
    consent, verifier = _authorize(c, client_id)
    back = _query(oauth_service.approve_request(_query(consent)["request"], user, allow_write))
    assert back["state"] == "st-1" and back["iss"] == settings.api_base_url
    r = c.post("/token", data={
        "grant_type": "authorization_code", "code": back["code"], "redirect_uri": REDIRECT,
        "client_id": client_id, "code_verifier": verifier,
    })
    assert r.status_code == 200, r.text
    return client_id, r.json()


def _call(c, access_token, tool, arguments=None):
    r = c.post("/mcp", headers={**MCP_HEADERS, "Authorization": f"Bearer {access_token}"}, json={
        "jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool, "arguments": arguments or {}},
    })
    return r.status_code, (r.json()["result"] if r.status_code == 200 else None)


def _refresh(c, client_id, refresh_token):
    return c.post("/token", data={"grant_type": "refresh_token", "refresh_token": refresh_token, "client_id": client_id})


def test_an_agent_signs_in_and_sees_only_what_its_user_sees(real_db, mcp):
    me, _other, mine, theirs, _asset = _seed(real_db)
    _client_id, tokens = _connect(mcp, me)

    assert set(tokens["scope"].split()) == {oauth_service.SCOPE_READ, oauth_service.SCOPE_WRITE}
    status, result = _call(mcp, tokens["access_token"], "list_projects")
    assert status == 200 and not result["isError"]
    names = {p["name"] for p in result["structuredContent"]["projects"]}
    assert mine.name in names and theirs.name not in names

    # Another user's private project: the router's own 403, as a tool error.
    status, result = _call(mcp, tokens["access_token"], "get_folder_tree", {"project_id": str(theirs.id)})
    assert status == 200 and result["isError"]


def test_mcp_refuses_requests_without_a_valid_token(real_db, mcp):
    r = mcp.post("/mcp", headers=MCP_HEADERS, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert r.status_code == 401
    assert "resource_metadata=" in r.headers["www-authenticate"]
    assert _call(mcp, "ffat_not-a-real-token", "whoami")[0] == 401


def test_assets_say_which_folder_they_are_in(real_db, mcp):
    from datetime import datetime, timezone
    from apps.api.models.asset import Asset, AssetType
    from apps.api.models.folder import Folder

    me, _other, mine, _theirs, top_level = _seed(real_db)
    footage = Folder(project_id=mine.id, name="Footage", created_by=me.id)
    real_db.add(footage); real_db.flush()
    drone = Folder(project_id=mine.id, parent_id=footage.id, name="Drone", created_by=me.id)
    trashed = Folder(project_id=mine.id, name="Old", created_by=me.id, deleted_at=datetime.now(timezone.utc))
    real_db.add_all([drone, trashed]); real_db.flush()
    under_trash = Folder(project_id=mine.id, parent_id=trashed.id, name="Kept", created_by=me.id)
    real_db.add(under_trash); real_db.flush()
    nested = Asset(project_id=mine.id, name="clip-nested", asset_type=AssetType.video, folder_id=drone.id, created_by=me.id)
    orphaned = Asset(project_id=mine.id, name="clip-orphaned", asset_type=AssetType.video, folder_id=under_trash.id, created_by=me.id)
    real_db.add_all([nested, orphaned]); real_db.flush()
    _client_id, tokens = _connect(mcp, me)

    _status, result = _call(mcp, tokens["access_token"], "search_assets", {"query": "clip"})
    paths = {a["name"]: a["folder_path"] for a in result["structuredContent"]["assets"]}
    assert paths["clip-nested"] == "Footage/Drone"
    assert paths[top_level.name] == ""
    assert paths["clip-orphaned"] is None



def test_a_refresh_token_works_once_and_replaces_both_tokens(real_db, mcp):
    me, *_ = _seed(real_db)
    client_id, first = _connect(mcp, me)

    r = _refresh(mcp, client_id, first["refresh_token"])
    assert r.status_code == 200
    second = r.json()

    reused = _refresh(mcp, client_id, first["refresh_token"])
    assert reused.status_code == 400 and reused.json()["error"] == "invalid_grant"
    assert _call(mcp, first["access_token"], "whoami")[0] == 401
    assert _call(mcp, second["access_token"], "whoami")[0] == 200


def test_a_refresh_token_only_works_for_the_client_it_was_issued_to(real_db, mcp):
    me, *_ = _seed(real_db)
    _client_id, tokens = _connect(mcp, me)
    other_client = _register(mcp)

    r = _refresh(mcp, other_client, tokens["refresh_token"])
    assert r.status_code == 400 and r.json()["error"] == "invalid_grant"


def test_a_read_only_connection_cannot_comment(real_db, mcp):
    from apps.api.models.comment import Comment

    me, _other, _mine, _theirs, asset = _seed(real_db)
    _client_id, tokens = _connect(mcp, me, allow_write=False)
    assert tokens["scope"] == oauth_service.SCOPE_READ

    _status, result = _call(mcp, tokens["access_token"], "add_comment", {"asset_id": str(asset.id), "body": "hi"})
    assert result["isError"] and "read-only" in result["content"][0]["text"]
    assert real_db.query(Comment).filter(Comment.asset_id == asset.id).count() == 0


def test_a_connection_with_changes_allowed_comments_as_its_user(real_db, mcp):
    from apps.api.models.comment import Comment

    me, _other, _mine, _theirs, asset = _seed(real_db)
    _client_id, tokens = _connect(mcp, me)

    _status, result = _call(mcp, tokens["access_token"], "add_comment", {
        "asset_id": str(asset.id), "body": "warm at 0:10", "timecode_start": 10,
    })
    assert not result["isError"], result
    row = real_db.query(Comment).filter(Comment.asset_id == asset.id).one()
    assert row.author_id == me.id and row.timecode_start == 10


@pytest.mark.parametrize("end_account", ["password_change", "deactivation"])
def test_a_connection_ends_with_the_accounts_session(real_db, mcp, end_account):
    from apps.api.models.user import UserStatus

    me, *_ = _seed(real_db)
    client_id, tokens = _connect(mcp, me)
    if end_account == "password_change":
        me.token_version += 1
    else:
        me.status = UserStatus.deactivated
    real_db.flush()

    assert _call(mcp, tokens["access_token"], "whoami")[0] == 401
    assert _refresh(mcp, client_id, tokens["refresh_token"]).status_code == 400


def test_revoking_a_connection_is_limited_to_its_owner_and_ends_it(real_db, mcp):
    me, other, *_ = _seed(real_db)
    client_id, tokens = _connect(mcp, me)
    [(grant, _client)] = oauth_service.list_user_grants(real_db, me.id)

    assert oauth_service.revoke_user_grant(real_db, other.id, grant.id) is False
    assert _call(mcp, tokens["access_token"], "whoami")[0] == 200

    assert oauth_service.revoke_user_grant(real_db, me.id, grant.id) is True
    assert _call(mcp, tokens["access_token"], "whoami")[0] == 401
    assert _refresh(mcp, client_id, tokens["refresh_token"]).status_code == 400
    assert oauth_service.list_user_grants(real_db, me.id) == []


def test_a_code_needs_the_pkce_verifier_and_works_once(real_db, mcp):
    me, *_ = _seed(real_db)
    client_id = _register(mcp)
    consent, verifier = _authorize(mcp, client_id)
    code = _query(oauth_service.approve_request(_query(consent)["request"], me, True))["code"]
    redeem = {"grant_type": "authorization_code", "code": code, "redirect_uri": REDIRECT, "client_id": client_id}

    wrong = mcp.post("/token", data={**redeem, "code_verifier": secrets.token_urlsafe(48)})
    assert wrong.status_code == 400 and wrong.json()["error"] == "invalid_grant"
    # The failed attempt used the code up.
    again = mcp.post("/token", data={**redeem, "code_verifier": verifier})
    assert again.status_code == 400 and again.json()["error"] == "invalid_grant"


def test_authorize_refuses_tokens_for_another_resource(real_db, mcp):
    client_id = _register(mcp)
    r = mcp.get("/authorize", params={
        "client_id": client_id, "response_type": "code", "redirect_uri": REDIRECT, "state": "s",
        "code_challenge": "x" * 43, "code_challenge_method": "S256", "resource": "https://elsewhere.example/mcp",
    }, follow_redirects=False)
    assert r.status_code == 302
    assert _query(r.headers["location"])["error"] == "invalid_target"


def test_discovery_metadata_on_the_api_names_this_server(client):
    """Through the main app: the MCP paths are routed and agree with each other."""
    resource_path = urlparse(settings.mcp_url).path
    prm = client.get(f"/.well-known/oauth-protected-resource{resource_path}")
    assert prm.status_code == 200
    assert prm.json()["resource"] == settings.mcp_url
    assert prm.json()["authorization_servers"] == [settings.api_base_url]

    # Bare, and RFC 8414's path-inserted form clients build from an issuer such as https://host/api.
    issuer_path = urlparse(settings.api_base_url).path
    for path in ("/.well-known/oauth-authorization-server", f"/.well-known/oauth-authorization-server{issuer_path}"):
        issuer = client.get(path).json()
        assert issuer["issuer"] == settings.api_base_url
        assert issuer["token_endpoint"] == f"{settings.api_base_url}/token"


def test_consent_answers_once(client, fake_redis, test_user, auth_headers):
    test_user.token_version = 1
    request_id = oauth_service.park_request(
        OAuthClientInformationFull(client_id="c1", client_name="Agent", redirect_uris=[REDIRECT]),
        scopes=None, code_challenge="x" * 43, redirect_uri=REDIRECT, redirect_uri_provided_explicitly=True,
        state="s1",
    )

    shown = client.get(f"/oauth/requests/{request_id}", headers=auth_headers)
    assert shown.status_code == 200 and shown.json()["client_name"] == "Agent"

    denied = client.post(f"/oauth/requests/{request_id}/deny", headers=auth_headers)
    assert denied.status_code == 200
    assert _query(denied.json()["redirect_url"]) == {
        "error": "access_denied", "error_description": "The user declined the connection",
        "state": "s1", "iss": settings.api_base_url,
    }
    late = client.post(f"/oauth/requests/{request_id}/approve", headers=auth_headers, json={"allow_write": True})
    assert late.status_code == 404


@pytest.mark.parametrize("base, disabled", [
    ("https://media.example.com/api", False),
    ("http://localhost:8000", False),
    ("http://192.168.1.50:8000", True),
])
def test_the_mcp_server_needs_an_https_or_localhost_address(monkeypatch, base, disabled):
    monkeypatch.setattr(settings, "public_api_url", base)
    assert (mcp_disabled_reason() is not None) is disabled


# ─── Uploads ──────────────────────────────────────────────────────────────────

MIB = 1024 * 1024


@pytest.fixture
def storage(monkeypatch):
    """Object storage, as the upload router and the discard cleanup see it."""
    from apps.api.routers import upload as upload_router
    from apps.api.tasks import cleanup_tasks

    state = {"parts": [], "transcodes": []}
    monkeypatch.setattr(upload_router, "create_multipart_upload", lambda key, mime: "mpu-1")
    monkeypatch.setattr(upload_router, "presign_upload_part", lambda key, uid, n: f"https://storage.test/{key}?part={n}")
    monkeypatch.setattr(upload_router, "list_upload_parts", lambda key, uid: list(state["parts"]))
    monkeypatch.setattr(upload_router, "complete_multipart_upload", lambda key, uid, parts: None)
    monkeypatch.setattr(upload_router, "head_object_size", lambda key: None)
    monkeypatch.setattr(upload_router, "abort_multipart_upload", lambda key, uid: None)
    monkeypatch.setattr(upload_router, "_trigger_processing", lambda a, v: state["transcodes"].append((a, v)))
    for name in ("abort_multipart_upload", "delete_object", "delete_prefix"):
        monkeypatch.setattr(cleanup_tasks, name, lambda *a, **k: None)
    return state


def _ticket(upload_command):
    parts_url = next(line for line in upload_command.splitlines() if line.startswith("PARTS="))
    return parts_url.split("/upload/agent/")[1].split("/")[0]


def _part_url(real_db, ticket, n=1):
    from fastapi import HTTPException
    from apps.api.routers.upload import presign_agent_part

    try:
        return presign_agent_part(ticket, n, db=real_db).body.decode()
    except HTTPException as exc:
        return exc.status_code


def test_an_agent_uploads_a_file_into_a_folder(real_db, mcp, storage):
    from apps.api.models.asset import Asset, MediaFile
    from apps.api.models.folder import Folder

    me, _other, mine, _theirs, _asset = _seed(real_db)
    folder = Folder(project_id=mine.id, name="Incoming", created_by=me.id)
    real_db.add(folder); real_db.flush()
    _client_id, tokens = _connect(mcp, me)

    _status, started = _call(mcp, tokens["access_token"], "start_upload", {
        "project_id": str(mine.id), "folder_id": str(folder.id),
        "file_path": "/home/me/Footage/Day 2.mov", "file_size_bytes": 25 * MIB,
    })
    assert not started["isError"], started
    up = started["structuredContent"]
    assert (up["part_size_bytes"], up["part_count"]) == (10 * MIB, 3)
    assert "FILE='/home/me/Footage/Day 2.mov'" in up["upload_command"]
    assert _part_url(real_db, _ticket(up["upload_command"]), 3).endswith("?part=3")

    _status, early = _call(mcp, tokens["access_token"], "finish_upload", {"version_id": up["version_id"]})
    assert early["isError"] and "No uploaded parts" in early["content"][0]["text"]
    assert storage["transcodes"] == []

    storage["parts"] = [
        {"PartNumber": 1, "ETag": "a", "Size": 10 * MIB},
        {"PartNumber": 2, "ETag": "b", "Size": 10 * MIB},
        {"PartNumber": 3, "ETag": "c", "Size": 5 * MIB},
    ]
    _status, done = _call(mcp, tokens["access_token"], "finish_upload", {"version_id": up["version_id"]})
    assert not done["isError"], done
    assert done["structuredContent"]["status"] == "processing"
    assert storage["transcodes"] == [(uuid.UUID(up["asset_id"]), uuid.UUID(up["version_id"]))]

    asset = real_db.get(Asset, uuid.UUID(up["asset_id"]))
    media = real_db.query(MediaFile).filter(MediaFile.version_id == uuid.UUID(up["version_id"])).one()
    assert (asset.name, asset.folder_id) == ("Day 2.mov", folder.id)
    assert (media.original_filename, media.mime_type) == ("Day 2.mov", "video/quicktime")


def test_uploading_needs_changes_allowed_and_the_editor_role(real_db, mcp, storage):
    from apps.api.models.project import ProjectMember, ProjectRole

    me, _other, mine, theirs, _asset = _seed(real_db)
    real_db.add(ProjectMember(project_id=theirs.id, user_id=me.id, role=ProjectRole.viewer)); real_db.flush()
    request = {"file_path": "clip.mp4", "file_size_bytes": MIB}

    _client_id, read_only = _connect(mcp, me, allow_write=False)
    _status, refused = _call(mcp, read_only["access_token"], "start_upload", {**request, "project_id": str(mine.id)})
    assert refused["isError"] and "read-only" in refused["content"][0]["text"]

    _client_id, writer = _connect(mcp, me)
    _status, viewer = _call(mcp, writer["access_token"], "start_upload", {**request, "project_id": str(theirs.id)})
    assert viewer["isError"] and "editor" in viewer["content"][0]["text"].lower()


def test_an_upload_link_ends_with_the_connection_it_came_from(real_db, mcp, storage):
    from apps.api.services.auth_service import create_access_token

    me, _other, mine, _theirs, _asset = _seed(real_db)
    _client_id, tokens = _connect(mcp, me)
    _status, started = _call(mcp, tokens["access_token"], "start_upload", {
        "project_id": str(mine.id), "file_path": "clip.mp4", "file_size_bytes": MIB,
    })
    ticket = _ticket(started["structuredContent"]["upload_command"])
    assert _part_url(real_db, ticket).startswith("https://storage.test/")

    # A sign-in token is not an upload ticket.
    assert _part_url(real_db, create_access_token(str(me.id), 1)) == 401

    [(grant, _client)] = oauth_service.list_user_grants(real_db, me.id)
    oauth_service.revoke_user_grant(real_db, me.id, grant.id)
    assert _part_url(real_db, ticket) == 401


def test_cancelling_an_upload_throws_it_away(real_db, mcp, storage):
    from apps.api.models.asset import Asset

    me, _other, mine, _theirs, _asset = _seed(real_db)
    _client_id, tokens = _connect(mcp, me)
    _status, started = _call(mcp, tokens["access_token"], "start_upload", {
        "project_id": str(mine.id), "file_path": "clip.mp4", "file_size_bytes": MIB,
    })
    up = started["structuredContent"]

    _status, cancelled = _call(mcp, tokens["access_token"], "cancel_upload", {"version_id": up["version_id"]})
    assert not cancelled["isError"], cancelled
    assert real_db.get(Asset, uuid.UUID(up["asset_id"])).deleted_at is not None
    assert _part_url(real_db, _ticket(up["upload_command"])) == 404


@pytest.mark.skipif(not (shutil.which("bash") and shutil.which("dd")), reason="needs bash and dd")
def test_the_upload_command_sends_the_file_cut_on_the_upload_parts(tmp_path):
    """Runs the generated script with curl replaced by a stub that keeps each PUT's body."""
    from apps.api.services.mcp_server import _upload_command

    source = tmp_path / "it's a clip.mov"
    source.write_bytes(os.urandom(2500))
    received = tmp_path / "received"
    received.mkdir()
    stub = tmp_path / "bin" / "curl"
    stub.parent.mkdir()
    stub.write_text(
        "#!/bin/sh\n"
        'url=""; put=0\n'
        'for a in "$@"; do case "$a" in PUT) put=1;; http*) url="$a";; esac; done\n'
        'if [ "$put" = 1 ]; then cat > "$RECEIVED/${url##*/}"; else echo "http://storage.test/${url##*/}"; fi\n'
    )
    stub.chmod(0o755)

    script = _upload_command(str(source), "https://api.test/upload/agent/T/parts", 1000, 3)
    run = subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True,
        env={**os.environ, "PATH": f"{stub.parent}:{os.environ['PATH']}", "RECEIVED": str(received)},
    )
    assert run.returncode == 0, run.stderr
    parts = [(received / str(n)).read_bytes() for n in (1, 2, 3)]
    assert [len(p) for p in parts] == [1000, 1000, 500]
    assert b"".join(parts) == source.read_bytes()
