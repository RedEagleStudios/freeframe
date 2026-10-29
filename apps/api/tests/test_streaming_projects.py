"""Preview while processing, streaming projects, and share-link embeds.

Driven through the real app on the rolled-back `real_db` transaction, so the
share scoping, the status rules and the retention query run against Postgres.
Storage is never reached: signing and deleting are recorded.
"""
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import apps.api.tasks.cleanup_tasks as ct
from apps.api.config import settings
from apps.api.models.asset import (
    Asset, AssetType, AssetVersion, FileType, MediaFile, ProcessingStatus,
)
from apps.api.models.project import Project, ProjectMember, ProjectRole, ProjectType
from apps.api.models.share import ShareLink
from apps.api.models.user import User, UserStatus


def _sign(key, *a, **k):
    return f"https://s3.test/{key}?sig=1"


@pytest.fixture
def api(real_db, monkeypatch):
    from fastapi.testclient import TestClient
    from apps.api.main import app
    from apps.api.database import get_db
    from apps.api.middleware.auth import get_current_user, get_optional_user

    for target in (
        "apps.api.routers.assets.generate_presigned_get_url",
        "apps.api.routers.share.generate_presigned_get_url",
        "apps.api.services.streaming.generate_presigned_get_url",
    ):
        monkeypatch.setattr(target, _sign)
    # Every caller here shares one IP; the share limits are not under test.
    monkeypatch.setattr("apps.api.middleware.rate_limit.client_ip", lambda r: str(uuid.uuid4()))
    who = {"user": None}
    app.dependency_overrides[get_db] = lambda: real_db
    app.dependency_overrides[get_current_user] = lambda: who["user"]
    app.dependency_overrides[get_optional_user] = lambda: who["user"]
    with patch("apps.api.services.s3_service.ensure_bucket_exists"):
        client = TestClient(app, raise_server_exceptions=False)

        def as_user(u):
            who["user"] = u
            return client

        yield as_user
    app.dependency_overrides.clear()


def _user(db, name="u"):
    u = User(email=f"{name}-{uuid.uuid4()}@t.local", name=name, status=UserStatus.active)
    db.add(u); db.flush()
    return u


def _project(db, owner, streaming=False, role=ProjectRole.owner):
    p = Project(name="p", project_type=ProjectType.personal, created_by=owner.id,
                streaming_mode=streaming)
    db.add(p); db.flush()
    db.add(ProjectMember(project_id=p.id, user_id=owner.id, role=role)); db.flush()
    return p


def _asset(db, project, owner, asset_type=AssetType.video, name="clip"):
    a = Asset(project_id=project.id, name=name, asset_type=asset_type, created_by=owner.id)
    db.add(a); db.flush()
    return a


def _version(db, asset, owner, status, n=1, ready_days_ago=None, mime="video/mp4",
             download=True, processed=True, thumb=True, raw_deleted=False):
    v = AssetVersion(asset_id=asset.id, version_number=n, processing_status=status,
                     created_by=owner.id)
    if ready_days_ago is not None:
        v.ready_at = datetime.now(timezone.utc) - timedelta(days=ready_days_ago)
    db.add(v); db.flush()
    prefix = f"processed/{asset.project_id}/{asset.id}/{v.id}"
    mf = MediaFile(
        version_id=v.id, file_type=FileType.video, original_filename="clip.mp4",
        mime_type=mime, file_size_bytes=1000,
        s3_key_raw=f"raw/{asset.project_id}/{asset.id}/{v.id}/original.mp4",
        s3_key_processed=prefix if processed else None,
        s3_key_download=f"{prefix}/download.mp4" if download and processed else None,
        s3_key_thumbnail=f"{prefix}/thumbnail.jpg" if thumb and processed else None,
        width=1920, height=1080,
        raw_deleted_at=datetime.now(timezone.utc) if raw_deleted else None,
    )
    db.add(mf); db.flush()
    return v, mf


def _link(db, owner, asset=None, project=None, **kw):
    link = ShareLink(token=f"tok-{uuid.uuid4()}", created_by=owner.id,
                     asset_id=asset.id if asset else None,
                     project_id=project.id if project else None, **kw)
    db.add(link); db.flush()
    return link


# ── preview while processing: member stream ─────────────────────────────────

def test_a_processing_mp4_streams_its_original_progressively(api, real_db):
    owner = _user(real_db)
    asset = _asset(real_db, _project(real_db, owner), owner)
    v, mf = _version(real_db, asset, owner, ProcessingStatus.processing, processed=False)

    r = api(owner).get(f"/assets/{asset.id}/stream?version_id={v.id}")

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["kind"] == "progressive" and body["processing"] is True
    assert body["url"] == _sign(mf.s3_key_raw)
    assert body["asset_type"] == "video" and body["expires_in"] == 3600


def test_with_no_version_named_a_lone_processing_version_previews(api, real_db):
    owner = _user(real_db)
    asset = _asset(real_db, _project(real_db, owner), owner)
    _version(real_db, asset, owner, ProcessingStatus.processing, processed=False)

    r = api(owner).get(f"/assets/{asset.id}/stream")

    assert r.status_code == 200 and r.json()["kind"] == "progressive"


@pytest.mark.parametrize("status", [ProcessingStatus.uploading, ProcessingStatus.failed])
def test_uploading_and_failed_versions_are_still_not_ready(api, real_db, status):
    owner = _user(real_db)
    asset = _asset(real_db, _project(real_db, owner), owner)
    v, _ = _version(real_db, asset, owner, status, processed=False)

    r = api(owner).get(f"/assets/{asset.id}/stream?version_id={v.id}")

    assert r.status_code == 409


def test_an_original_no_browser_plays_is_not_previewed(api, real_db):
    owner = _user(real_db)
    asset = _asset(real_db, _project(real_db, owner), owner)
    v, _ = _version(real_db, asset, owner, ProcessingStatus.processing, processed=False,
                    mime="video/x-msvideo")

    assert api(owner).get(f"/assets/{asset.id}/stream?version_id={v.id}").status_code == 409


def test_a_deleted_original_is_never_previewed(api, real_db):
    owner = _user(real_db)
    asset = _asset(real_db, _project(real_db, owner), owner)
    v, _ = _version(real_db, asset, owner, ProcessingStatus.processing, processed=False,
                    raw_deleted=True)

    assert api(owner).get(f"/assets/{asset.id}/stream?version_id={v.id}").status_code == 409


def test_a_processing_version_is_never_a_download(api, real_db):
    owner = _user(real_db)
    asset = _asset(real_db, _project(real_db, owner), owner)
    v, _ = _version(real_db, asset, owner, ProcessingStatus.processing, processed=False)

    r = api(owner).get(f"/assets/{asset.id}/stream?version_id={v.id}&download=true")

    assert r.status_code == 409


def test_a_ready_video_says_hls(api, real_db):
    owner = _user(real_db)
    asset = _asset(real_db, _project(real_db, owner), owner)
    v, _ = _version(real_db, asset, owner, ProcessingStatus.ready)

    body = api(owner).get(f"/assets/{asset.id}/stream?version_id={v.id}").json()

    assert body["kind"] == "hls" and body["processing"] is False
    assert body["url"].startswith("/stream/hls/master.m3u8?token=")


def test_a_preview_needs_access_to_the_asset(api, real_db):
    owner, stranger = _user(real_db), _user(real_db, "x")
    asset = _asset(real_db, _project(real_db, owner), owner)
    v, _ = _version(real_db, asset, owner, ProcessingStatus.processing, processed=False)

    r = api(stranger).get(f"/assets/{asset.id}/stream?version_id={v.id}")

    assert r.status_code in (403, 404)


# ── preview while processing: share links ───────────────────────────────────

def test_a_share_previews_the_newest_processing_version_when_none_is_ready(api, real_db):
    owner = _user(real_db)
    project = _project(real_db, owner)
    asset = _asset(real_db, project, owner)
    v1, mf1 = _version(real_db, asset, owner, ProcessingStatus.processing, n=1, processed=False)
    _version(real_db, asset, owner, ProcessingStatus.uploading, n=2, processed=False)
    link = _link(real_db, owner, asset=asset)

    r = api(None).get(f"/share/{link.token}/stream/{asset.id}")

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["kind"] == "progressive" and body["processing"] is True
    assert body["version_id"] == str(v1.id)
    assert body["url"] == _sign(mf1.s3_key_raw)


def test_a_share_with_a_ready_version_keeps_serving_it(api, real_db):
    owner = _user(real_db)
    project = _project(real_db, owner)
    asset = _asset(real_db, project, owner)
    v1, _ = _version(real_db, asset, owner, ProcessingStatus.ready, n=1)
    v2, _ = _version(real_db, asset, owner, ProcessingStatus.processing, n=2, processed=False)
    link = _link(real_db, owner, asset=asset, show_versions=True)

    # Even naming the processing version does not reach it.
    body = api(None).get(f"/share/{link.token}/stream/{asset.id}?version_id={v2.id}").json()

    assert body["kind"] == "hls" and body["version_id"] == str(v1.id)


def test_a_share_never_previews_an_asset_outside_its_scope(api, real_db):
    owner = _user(real_db)
    project = _project(real_db, owner)
    shared, other = _asset(real_db, project, owner), _asset(real_db, project, owner, name="b")
    _version(real_db, shared, owner, ProcessingStatus.ready)
    _version(real_db, other, owner, ProcessingStatus.processing, processed=False)
    link = _link(real_db, owner, asset=shared)

    assert api(None).get(f"/share/{link.token}/stream/{other.id}").status_code == 403


def test_a_share_does_not_preview_a_deleted_original_or_a_download(api, real_db):
    owner = _user(real_db)
    project = _project(real_db, owner)
    asset = _asset(real_db, project, owner)
    _version(real_db, asset, owner, ProcessingStatus.processing, processed=False)
    link = _link(real_db, owner, asset=asset, allow_download=True)
    client = api(None)

    assert client.get(f"/share/{link.token}/stream/{asset.id}?download=true").status_code == 404

    real_db.query(MediaFile).update({MediaFile.raw_deleted_at: datetime.now(timezone.utc)})
    real_db.flush()
    assert client.get(f"/share/{link.token}/stream/{asset.id}").status_code == 404


def test_a_secure_share_still_needs_sign_in_for_a_preview(api, real_db):
    owner = _user(real_db)
    asset = _asset(real_db, _project(real_db, owner), owner)
    _version(real_db, asset, owner, ProcessingStatus.processing, processed=False)
    link = _link(real_db, owner, asset=asset, visibility="secure")

    assert api(None).get(f"/share/{link.token}/stream/{asset.id}").status_code == 403


# ── streaming mode setting ──────────────────────────────────────────────────

def test_only_an_owner_can_turn_streaming_on(api, real_db):
    owner, editor = _user(real_db), _user(real_db, "e")
    project = _project(real_db, owner)
    real_db.add(ProjectMember(project_id=project.id, user_id=editor.id, role=ProjectRole.editor))
    real_db.flush()

    assert api(editor).patch(f"/projects/{project.id}", json={"streaming_mode": True}).status_code == 403
    r = api(owner).patch(f"/projects/{project.id}", json={"streaming_mode": True})

    assert r.status_code == 200, r.text
    assert r.json()["streaming_mode"] is True
    assert r.json()["streaming_retention_days"] == settings.streaming_original_retention_days
    real_db.refresh(project)
    assert project.streaming_mode is True


def test_the_project_list_reports_streaming_mode(api, real_db):
    owner = _user(real_db)
    project = _project(real_db, owner, streaming=True)

    rows = api(owner).get("/projects").json()

    assert next(p for p in rows if p["id"] == str(project.id))["streaming_mode"] is True


def test_a_negative_retention_is_refused():
    from pydantic import ValidationError
    from apps.api.config import Settings

    with pytest.raises(ValidationError):
        Settings(jwt_secret="x", streaming_original_retention_days=-1)


# ── transcode asks for the MP4 in a streaming project ───────────────────────

def _transcode_job(monkeypatch, streaming_mode):
    from apps.api.tasks import transcode_tasks
    from packages.transcoder.base import TranscodeResult

    monkeypatch.setattr(settings, "transcoder_download_mp4", False)
    asset = SimpleNamespace(id="a", project_id="p", asset_type=AssetType.video)
    version = SimpleNamespace(id="v")
    media_file = SimpleNamespace(s3_key_raw="raw/x.mp4", s3_key_processed=None, s3_key_download=None,
                                 s3_key_thumbnail=None, duration_seconds=None)
    with patch("packages.transcoder.ffmpeg_transcoder.FFmpegTranscoder") as cls:
        cls.return_value.transcode = MagicMock(return_value=None)
        monkeypatch.setattr(transcode_tasks, "_run_async",
                            lambda coro: TranscodeResult(success=True, hls_prefix="processed/x",
                                                         mp4_key="processed/x/download.mp4"))
        transcode_tasks._process_video(MagicMock(), asset, version, media_file, MagicMock(),
                                       "processed/x", streaming_mode=streaming_mode)
        return cls.return_value.transcode.call_args[0][0]


def test_a_streaming_project_gets_the_mp4_with_the_instance_flag_off(monkeypatch):
    assert _transcode_job(monkeypatch, streaming_mode=True).download_mp4 is True
    assert _transcode_job(monkeypatch, streaming_mode=False).download_mp4 is False


def test_process_asset_reads_the_projects_streaming_mode(real_db, monkeypatch):
    from apps.api.tasks import transcode_tasks

    owner = _user(real_db)
    asset = _asset(real_db, _project(real_db, owner, streaming=True), owner)
    v, _ = _version(real_db, asset, owner, ProcessingStatus.processing, processed=False)
    seen = {}

    def fake_process_video(db, a, ver, mf, s3, prefix, *, final_attempt=False, streaming_mode=False):
        seen["streaming_mode"] = streaming_mode

    monkeypatch.setattr(transcode_tasks, "SessionLocal", lambda: real_db)
    monkeypatch.setattr(real_db, "close", lambda: None)
    monkeypatch.setattr(real_db, "commit", real_db.flush)
    monkeypatch.setattr(transcode_tasks, "get_s3_client", MagicMock)
    monkeypatch.setattr(transcode_tasks, "_process_video", fake_process_video)
    monkeypatch.setattr(transcode_tasks, "_publish_event", lambda *a, **k: True)

    transcode_tasks.process_asset.run(str(asset.id), str(v.id))

    assert seen["streaming_mode"] is True
    real_db.refresh(v)
    assert v.processing_status == ProcessingStatus.ready and v.ready_at is not None


def _process_with_early_meta(real_db, monkeypatch, api, early_meta, *, fail_commit=False):
    """Run process_asset on a processing video whose fake transcode reports
    `early_meta` through the job's callback, and return what the share embed
    said at that moment plus the commits made by then."""
    from apps.api.tasks import transcode_tasks
    from packages.transcoder.base import TranscodeResult

    owner = _user(real_db)
    asset = _asset(real_db, _project(real_db, owner), owner)
    v, mf = _version(real_db, asset, owner, ProcessingStatus.processing, processed=False)
    mf.width = mf.height = None
    real_db.flush()
    link = _link(real_db, owner, asset=asset)
    prefix = f"processed/{asset.project_id}/{asset.id}/{v.id}"
    seen = {"commits": 0}

    def commit():
        if fail_commit and seen.get("in_early"):
            raise RuntimeError("database went away")
        seen["commits"] += 1
        real_db.flush()

    class FakeTranscoder:
        def __init__(self, *a, **k):
            pass

        async def transcode(self, job):
            seen["in_early"] = True
            job.early_meta_cb(*early_meta(prefix))
            seen["in_early"] = False
            seen["commits_after_early"] = seen["commits"]
            seen["embed"] = api(None).get(f"/share/{link.token}/embed").json()
            return TranscodeResult(success=True, hls_prefix=prefix, mp4_key=f"{prefix}/download.mp4",
                                   thumbnail_keys=[f"{prefix}/thumbnail.jpg"],
                                   duration_seconds=12.5, width=1920, height=1080, fps=25.0)

    monkeypatch.setattr("packages.transcoder.ffmpeg_transcoder.FFmpegTranscoder", FakeTranscoder)
    monkeypatch.setattr(transcode_tasks, "SessionLocal", lambda: real_db)
    monkeypatch.setattr(real_db, "close", lambda: None)
    monkeypatch.setattr(real_db, "commit", commit)
    monkeypatch.setattr(real_db, "rollback", lambda: None)
    monkeypatch.setattr(transcode_tasks, "get_s3_client", MagicMock)
    monkeypatch.setattr(transcode_tasks, "_publish_event", lambda *a, **k: True)

    transcode_tasks.process_asset.run(str(asset.id), str(v.id))
    real_db.refresh(v)
    return v, mf, link, prefix, seen


def test_the_early_poster_frame_and_size_are_committed_while_processing(real_db, monkeypatch, api):
    v, mf, link, prefix, seen = _process_with_early_meta(
        real_db, monkeypatch, api, lambda prefix: (1080, 1920, 12.5, f"{prefix}/thumbnail.jpg"))

    # process_asset commits `processing` once; the early callback commits again.
    assert seen["commits_after_early"] == 2
    assert seen["embed"]["image"] == {"url": f"/share/{link.token}/embed.jpg", "width": 1080, "height": 1920}
    assert seen["embed"]["video"]["width"] == 1080
    assert v.processing_status == ProcessingStatus.ready
    assert mf.s3_key_thumbnail == f"{prefix}/thumbnail.jpg" and mf.duration_seconds == 12.5


def test_early_metadata_the_probe_did_not_establish_is_left_alone(real_db, monkeypatch, api):
    _, _, _, _, seen = _process_with_early_meta(real_db, monkeypatch, api, lambda prefix: (None, None, None, None))

    assert seen["embed"]["image"] is None
    assert seen["embed"]["video"]["width"] is None and seen["embed"]["video"]["height"] is None


def test_a_failing_early_write_does_not_fail_the_transcode(real_db, monkeypatch, api):
    v, mf, _, prefix, _ = _process_with_early_meta(
        real_db, monkeypatch, api, lambda prefix: (1080, 1920, 12.5, f"{prefix}/thumbnail.jpg"),
        fail_commit=True)

    assert v.processing_status == ProcessingStatus.ready
    assert mf.s3_key_download == f"{prefix}/download.mp4" and (mf.width, mf.height) == (1920, 1080)



def test_a_version_whose_original_is_gone_is_not_retranscoded(real_db, monkeypatch):
    from apps.api.tasks import transcode_tasks

    owner = _user(real_db)
    asset = _asset(real_db, _project(real_db, owner, streaming=True), owner)
    v, _ = _version(real_db, asset, owner, ProcessingStatus.ready, raw_deleted=True)
    monkeypatch.setattr(transcode_tasks, "SessionLocal", lambda: real_db)
    monkeypatch.setattr(real_db, "close", lambda: None)
    monkeypatch.setattr(transcode_tasks, "_process_video",
                        lambda *a, **k: pytest.fail("must not transcode without an original"))

    transcode_tasks.process_asset.run(str(asset.id), str(v.id))

    real_db.refresh(v)
    assert v.processing_status == ProcessingStatus.ready


# ── downloads after the original is gone ────────────────────────────────────

def test_download_options_omit_a_deleted_original(api, real_db):
    owner = _user(real_db)
    asset = _asset(real_db, _project(real_db, owner, streaming=True), owner)
    v, _ = _version(real_db, asset, owner, ProcessingStatus.ready, raw_deleted=True)

    with patch("apps.api.services.downloads.describe_choices"):
        r = api(owner).get(f"/assets/{asset.id}/downloads?version_id={v.id}")
    assert r.status_code == 200, r.text
    assert [c["id"] for c in r.json()] == ["transcode"]

    r = api(owner).get(f"/assets/{asset.id}/stream?version_id={v.id}&download=true&rendition=original")
    assert r.status_code == 410

    r = api(owner).get(f"/assets/{asset.id}/stream?version_id={v.id}&download=true&prefer_original=true")
    assert r.status_code == 200 and "download.mp4" in r.json()["url"]


# ── the retention task ──────────────────────────────────────────────────────

@pytest.fixture
def storage(monkeypatch):
    calls = {"deleted": [], "queued": [], "refuse": set()}

    def delete(key):
        if key in calls["refuse"]:
            from botocore.exceptions import ClientError
            raise ClientError({"Error": {"Code": "ObjectLockedByBucketPolicy", "Message": "locked"}},
                              "DeleteObject")
        calls["deleted"].append(key)

    monkeypatch.setattr(ct, "delete_object", delete)
    monkeypatch.setattr("apps.api.tasks.celery_app.send_task_safe",
                        lambda task, *args: calls["queued"].append((task.name, args)))
    monkeypatch.setattr(settings, "streaming_original_retention_days", 30)
    return calls


def test_the_retention_task_deletes_only_eligible_originals(real_db, storage):
    owner = _user(real_db)
    streaming = _project(real_db, owner, streaming=True)
    review = _project(real_db, owner, streaming=False)

    a = _asset(real_db, streaming, owner)
    _, due = _version(real_db, a, owner, ProcessingStatus.ready, ready_days_ago=31)
    _, too_new = _version(real_db, a, owner, ProcessingStatus.ready, n=2, ready_days_ago=5)
    _, processing = _version(real_db, a, owner, ProcessingStatus.processing, n=3, ready_days_ago=40)
    v_del, deleted_version = _version(real_db, a, owner, ProcessingStatus.ready, n=4, ready_days_ago=40)
    v_del.deleted_at = datetime.now(timezone.utc)
    _, already = _version(real_db, a, owner, ProcessingStatus.ready, n=5, ready_days_ago=40,
                          raw_deleted=True)
    trashed_asset = _asset(real_db, streaming, owner, name="t")
    trashed_asset.deleted_at = datetime.now(timezone.utc)
    _, in_trashed_asset = _version(real_db, trashed_asset, owner, ProcessingStatus.ready, ready_days_ago=40)
    image = _asset(real_db, streaming, owner, asset_type=AssetType.image, name="i")
    _, still = _version(real_db, image, owner, ProcessingStatus.ready, ready_days_ago=40)
    b = _asset(real_db, review, owner)
    _, review_mf = _version(real_db, b, owner, ProcessingStatus.ready, ready_days_ago=400)
    real_db.flush()

    counts = ct._delete_streaming_originals(real_db)
    real_db.flush()  # what the task wrapper's commit writes

    assert storage["deleted"] == [due.s3_key_raw]
    assert counts.deleted == 1 and counts.deleted_bytes == 1000
    real_db.refresh(due)
    assert due.raw_deleted_at is not None
    for mf in (too_new, processing, deleted_version, in_trashed_asset, still, review_mf):
        real_db.refresh(mf)
        assert mf.raw_deleted_at is None


def test_a_locked_original_is_left_for_the_next_run(real_db, storage):
    owner = _user(real_db)
    a = _asset(real_db, _project(real_db, owner, streaming=True), owner)
    _, mf = _version(real_db, a, owner, ProcessingStatus.ready, ready_days_ago=31)
    storage["refuse"].add(mf.s3_key_raw)

    counts = ct._delete_streaming_originals(real_db)
    real_db.flush()

    assert counts.failed == 1 and counts.deleted == 0
    real_db.refresh(mf)
    assert mf.raw_deleted_at is None

    storage["refuse"].clear()
    ct._delete_streaming_originals(real_db)
    real_db.flush()
    real_db.refresh(mf)
    assert mf.raw_deleted_at is not None


def test_a_version_without_an_mp4_gets_one_built_before_its_original_goes(real_db, storage):
    owner = _user(real_db)
    a = _asset(real_db, _project(real_db, owner, streaming=True), owner)
    v, mf = _version(real_db, a, owner, ProcessingStatus.ready, ready_days_ago=31, download=False)

    counts = ct._delete_streaming_originals(real_db)

    assert storage["deleted"] == []
    assert storage["queued"] == [("apps.api.tasks.transcode_tasks.build_download_mp4", (str(v.id),))]
    assert counts.mp4_queued == 1
    real_db.refresh(mf)
    assert mf.raw_deleted_at is None


def test_retention_zero_disables_the_task(real_db, storage, monkeypatch):
    owner = _user(real_db)
    a = _asset(real_db, _project(real_db, owner, streaming=True), owner)
    _version(real_db, a, owner, ProcessingStatus.ready, ready_days_ago=400)
    monkeypatch.setattr(settings, "streaming_original_retention_days", 0)

    counts = ct._delete_streaming_originals(real_db)

    assert counts.retention_days == 0 and counts.eligible == 0
    assert storage["deleted"] == [] and storage["queued"] == []


def test_the_task_is_routed_to_maintenance_and_scheduled():
    from apps.api.tasks.celery_app import celery_app

    assert celery_app.conf.task_routes["delete_streaming_originals"] == {"queue": "maintenance"}
    assert any(e["task"] == "delete_streaming_originals" for e in celery_app.conf.beat_schedule.values())


def test_build_download_mp4_records_the_rebuilt_mp4(real_db, monkeypatch):
    from apps.api.tasks import transcode_tasks

    owner = _user(real_db)
    a = _asset(real_db, _project(real_db, owner, streaming=True), owner)
    v, mf = _version(real_db, a, owner, ProcessingStatus.ready, download=False)
    monkeypatch.setattr(transcode_tasks, "SessionLocal", lambda: real_db)
    monkeypatch.setattr(real_db, "close", lambda: None)
    monkeypatch.setattr(real_db, "commit", real_db.flush)
    monkeypatch.setattr(transcode_tasks, "get_s3_client", MagicMock)
    with patch("packages.transcoder.ffmpeg_transcoder.FFmpegTranscoder") as cls:
        cls.return_value.rebuild_download_mp4.side_effect = lambda prefix: f"{prefix}/download.mp4"
        key = transcode_tasks.build_download_mp4.run(str(v.id))

    assert key == f"{mf.s3_key_processed}/download.mp4"
    real_db.refresh(mf)
    assert mf.s3_key_download == key


def test_the_tallest_rung_is_picked_from_the_master_playlist():
    from packages.transcoder.ffmpeg_transcoder import download_mp4_command, tallest_variant_uri

    master = (
        "#EXTM3U\n"
        "#EXT-X-STREAM-INF:BANDWIDTH=900000,RESOLUTION=640x360\n0/playlist.m3u8\n"
        "#EXT-X-STREAM-INF:BANDWIDTH=5000000,RESOLUTION=1080x1920\n1/playlist.m3u8\n"
        "#EXT-X-STREAM-INF:BANDWIDTH=3000000,RESOLUTION=1280x720\n2/playlist.m3u8\n"
    )
    assert tallest_variant_uri(master) == "1/playlist.m3u8"
    assert tallest_variant_uri("#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1\n0/playlist.m3u8\n") == "0/playlist.m3u8"
    cmd = download_mp4_command("in.m3u8", "out.mp4")
    assert cmd[cmd.index("-c") + 1] == "copy" and "+faststart" in cmd


# ── share-link embeds ────────────────────────────────────────────────────────

def test_an_embed_describes_a_single_video_link(api, real_db):
    owner = _user(real_db)
    asset = _asset(real_db, _project(real_db, owner), owner, name="Launch film")
    _version(real_db, asset, owner, ProcessingStatus.ready)
    link = _link(real_db, owner, asset=asset)

    body = api(None).get(f"/share/{link.token}/embed").json()

    assert body["title"] == "Launch film" and body["asset_type"] == "video"
    assert body["image"] == {"url": f"/share/{link.token}/embed.jpg", "width": 1920, "height": 1080}
    assert body["video"]["url"] == f"/share/{link.token}/embed.mp4"
    assert body["video"]["type"] == "video/mp4"


@pytest.mark.parametrize("method", ["get", "head"])
def test_the_embed_media_redirect_to_fresh_presigned_urls(api, real_db, method):
    owner = _user(real_db)
    asset = _asset(real_db, _project(real_db, owner), owner)
    _, mf = _version(real_db, asset, owner, ProcessingStatus.ready)
    link = _link(real_db, owner, asset=asset)
    client = api(None)

    mp4 = getattr(client, method)(f"/share/{link.token}/embed.mp4", follow_redirects=False)
    jpg = getattr(client, method)(f"/share/{link.token}/embed.jpg", follow_redirects=False)

    assert mp4.status_code == 302 and mp4.headers["location"] == _sign(mf.s3_key_download)
    assert jpg.status_code == 302 and jpg.headers["location"] == _sign(mf.s3_key_thumbnail)
    assert mp4.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("kind", [
    "password", "secure", "expired", "disabled", "image", "no_mp4",
    "processing_password", "processing_secure", "processing_unplayable", "processing_deleted",
])
def test_embed_mp4_is_404_where_a_crawler_may_not_have_it(api, real_db, kind):
    owner = _user(real_db)
    asset = _asset(real_db, _project(real_db, owner), owner,
                   asset_type=AssetType.image if kind == "image" else AssetType.video)
    processing = kind.startswith("processing")
    _version(real_db, asset, owner,
             ProcessingStatus.processing if processing else ProcessingStatus.ready,
             download=kind != "no_mp4", processed=not processing,
             mime="video/x-matroska" if kind == "processing_unplayable" else "video/mp4",
             raw_deleted=kind == "processing_deleted")
    extra = {
        "password": {"password_hash": "x"},
        "secure": {"visibility": "secure"},
        "expired": {"expires_at": datetime.now(timezone.utc) - timedelta(days=1)},
        "disabled": {"is_enabled": False},
    }.get(kind.removeprefix("processing_"), {})
    link = _link(real_db, owner, asset=asset, **extra)
    client = api(None)

    assert client.get(f"/share/{link.token}/embed.mp4", follow_redirects=False).status_code == 404
    assert client.head(f"/share/{link.token}/embed.mp4", follow_redirects=False).status_code == 404
    embed = client.get(f"/share/{link.token}/embed")
    if kind.removeprefix("processing_") in ("password", "secure", "expired", "disabled"):
        assert embed.status_code == 404
        assert client.get(f"/share/{link.token}/embed.jpg", follow_redirects=False).status_code == 404
    else:
        assert embed.status_code == 200 and embed.json()["video"] is None


def test_a_processing_video_embeds_its_original_until_its_mp4_is_ready(api, real_db):
    owner = _user(real_db)
    asset = _asset(real_db, _project(real_db, owner), owner, name="Launch film")
    v, mf = _version(real_db, asset, owner, ProcessingStatus.processing, processed=False)
    # What the transcode records at its start (see _on_early_meta).
    mf.s3_key_thumbnail = f"processed/{asset.project_id}/{asset.id}/{v.id}/thumbnail.jpg"
    mf.width, mf.height = 1080, 1920
    real_db.flush()
    link = _link(real_db, owner, asset=asset)
    client = api(None)

    body = client.get(f"/share/{link.token}/embed").json()
    assert body["image"] == {"url": f"/share/{link.token}/embed.jpg", "width": 1080, "height": 1920}
    assert body["video"] == {"url": f"/share/{link.token}/embed.mp4", "secure_url": f"/share/{link.token}/embed.mp4",
                             "type": "video/mp4", "width": 1080, "height": 1920}
    mp4 = client.get(f"/share/{link.token}/embed.mp4", follow_redirects=False)
    assert mp4.status_code == 302 and mp4.headers["location"] == _sign(mf.s3_key_raw)
    assert mp4.headers["cache-control"] == "no-store"
    jpg = client.get(f"/share/{link.token}/embed.jpg", follow_redirects=False)
    assert jpg.status_code == 302 and jpg.headers["location"] == _sign(mf.s3_key_thumbnail)

    prefix = f"processed/{asset.project_id}/{asset.id}/{v.id}"
    v.processing_status = ProcessingStatus.ready
    mf.s3_key_processed, mf.s3_key_download = prefix, f"{prefix}/download.mp4"
    real_db.flush()

    mp4 = client.get(f"/share/{link.token}/embed.mp4", follow_redirects=False)
    assert mp4.status_code == 302 and mp4.headers["location"] == _sign(mf.s3_key_download)


def test_a_processing_embed_declares_the_originals_type_and_omits_unknown_sizes(api, real_db):
    owner = _user(real_db)
    asset = _asset(real_db, _project(real_db, owner), owner)
    _, mf = _version(real_db, asset, owner, ProcessingStatus.processing, processed=False,
                     mime="video/quicktime")
    mf.width = mf.height = None
    real_db.flush()
    link = _link(real_db, owner, asset=asset)
    client = api(None)

    body = client.get(f"/share/{link.token}/embed").json()
    # No poster frame yet: no image, rather than one that 404s.
    assert body["image"] is None
    assert body["video"]["type"] == "video/quicktime"
    assert body["video"]["width"] is None and body["video"]["height"] is None
    assert client.get(f"/share/{link.token}/embed.jpg", follow_redirects=False).status_code == 404


def test_a_ready_version_without_an_mp4_is_not_replaced_by_a_processing_original(api, real_db):
    owner = _user(real_db)
    asset = _asset(real_db, _project(real_db, owner), owner)
    _version(real_db, asset, owner, ProcessingStatus.ready, n=1, download=False)
    _version(real_db, asset, owner, ProcessingStatus.processing, n=2, processed=False)
    link = _link(real_db, owner, asset=asset)
    client = api(None)

    assert client.get(f"/share/{link.token}/embed").json()["video"] is None
    assert client.get(f"/share/{link.token}/embed.mp4", follow_redirects=False).status_code == 404


def test_a_project_link_embeds_a_title_only(api, real_db):
    owner = _user(real_db)
    project = _project(real_db, owner)
    asset = _asset(real_db, project, owner)
    _version(real_db, asset, owner, ProcessingStatus.ready)
    link = _link(real_db, owner, project=project)

    body = api(None).get(f"/share/{link.token}/embed").json()

    assert body["title"] == "p" and body["video"] is None and body["image"] is None
    assert api(None).get(f"/share/{link.token}/embed.mp4", follow_redirects=False).status_code == 404
