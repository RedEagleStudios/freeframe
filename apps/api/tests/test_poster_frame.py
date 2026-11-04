"""The poster frame the API makes when a video's upload completes (services/poster_frame.py).

Real ffmpeg on a real (generated) video and real Postgres; storage is a local
file for reading and a dict for writing.
"""
import shutil
import subprocess
import uuid

import pytest

pytestmark = pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="needs ffmpeg and ffprobe",
)


@pytest.fixture
def storage(monkeypatch, tmp_path):
    from apps.api.services import poster_frame

    video = tmp_path / "clip.mp4"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=320x180:rate=25:duration=2",
         "-pix_fmt", "yuv420p", str(video)],
        check=True,
    )
    written: dict[str, bytes] = {}
    monkeypatch.setattr(poster_frame, "generate_internal_presigned_get_url", lambda key, expires_in: str(video))
    monkeypatch.setattr(poster_frame, "put_object", lambda key, body, **kw: written.__setitem__(key, body))
    return written


def _version(real_db, asset_type):
    from apps.api.models.asset import Asset, AssetVersion, FileType, MediaFile, ProcessingStatus
    from apps.api.models.project import Project, ProjectType
    from apps.api.models.user import User

    user = User(email=f"poster-{uuid.uuid4()}@t.local", name="P")
    real_db.add(user); real_db.flush()
    project = Project(name="p", project_type=ProjectType.personal, created_by=user.id)
    real_db.add(project); real_db.flush()
    asset = Asset(project_id=project.id, name="clip", asset_type=asset_type, created_by=user.id)
    real_db.add(asset); real_db.flush()
    version = AssetVersion(asset_id=asset.id, version_number=1, processing_status=ProcessingStatus.processing,
                           created_by=user.id)
    real_db.add(version); real_db.flush()
    media = MediaFile(version_id=version.id, file_type=FileType.video, original_filename="clip.mp4",
                      mime_type="video/mp4", file_size_bytes=1, s3_key_raw=f"raw/{asset.id}/original.mp4")
    real_db.add(media); real_db.flush()
    return project, asset, version, media


def test_a_video_gets_its_poster_frame_and_size_as_soon_as_it_is_uploaded(real_db, storage):
    from apps.api.models.asset import AssetType
    from apps.api.services.poster_frame import make_poster

    project, asset, version, media = _version(real_db, AssetType.video)

    assert make_poster(real_db, version.id) is True
    real_db.refresh(media)
    key = f"processed/{project.id}/{asset.id}/{version.id}/thumbnail.jpg"
    assert media.s3_key_thumbnail == key
    assert storage[key][:2] == b"\xff\xd8"  # a JPEG
    assert (media.width, media.height) == (320, 180)
    assert media.duration_seconds == pytest.approx(2, abs=0.1)

    # Once there is a frame, a second run (or the transcode) is not overwritten here.
    assert make_poster(real_db, version.id) is False


def test_only_videos_get_one(real_db, storage):
    from apps.api.models.asset import AssetType
    from apps.api.services.poster_frame import make_poster

    _project, _asset, version, media = _version(real_db, AssetType.audio)
    assert make_poster(real_db, version.id) is False
    assert storage == {} and media.s3_key_thumbnail is None
