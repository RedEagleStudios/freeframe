"""A video's poster frame, made the moment its upload completes.

The transcode makes the poster frame when it starts, and a transcode waits
behind every video ahead of it in the queue. A share link pasted into Discord
in that wait unfurled with no picture: the preview fell back to reading a
frame from the original on the fly, which on a busy server took longer than
Discord waits, and Discord keeps the first answer it gets for a link.

So the API makes the frame itself, after answering /upload/complete: one
ffprobe and one ffmpeg over a presigned URL (a second or two, reading only
what they need), with the same filter and key the transcode uses. The
transcode later writes the same key again. Best-effort: a failure is logged
and the transcode's own frame arrives as before.
"""
import logging
import subprocess
import uuid
from typing import Optional

from sqlalchemy import func, update
from sqlalchemy.orm import Session

from ..database import SessionLocal
from ..models.asset import Asset, AssetType, AssetVersion, MediaFile
from .s3_service import generate_internal_presigned_get_url, put_object

logger = logging.getLogger(__name__)

_PROBE_TIMEOUT_SECONDS = 60
_FRAME_TIMEOUT_SECONDS = 120


def _probe(url: str) -> Optional[dict]:
    import json

    result = subprocess.run(
        [
            "ffprobe", "-v", "error", "-print_format", "json",
            "-show_streams", "-select_streams", "V:0", "-show_format", url,
        ],
        capture_output=True, text=True, timeout=_PROBE_TIMEOUT_SECONDS,
    )
    if result.returncode != 0:
        return None
    return json.loads(result.stdout or "{}")


def _frame(url: str, vf: str) -> Optional[bytes]:
    result = subprocess.run(
        [
            "ffmpeg", "-nostdin", "-v", "error", "-i", url,
            "-vf", vf, "-q:v", "2", "-frames:v", "1", "-f", "image2", "-c:v", "mjpeg", "pipe:1",
        ],
        capture_output=True, timeout=_FRAME_TIMEOUT_SECONDS,
    )
    if result.returncode != 0 or not result.stdout:
        return None
    return result.stdout


def make_poster(db: Session, version_id: uuid.UUID) -> bool:
    """Make and record the poster frame, width, height and duration of a video
    version that has none yet. True if this call recorded a frame."""
    from packages.transcoder.ffmpeg_transcoder import display_size, parse_probe_metadata, poster_filter, poster_key

    row = (
        db.query(Asset, MediaFile)
        .join(AssetVersion, AssetVersion.asset_id == Asset.id)
        .join(MediaFile, MediaFile.version_id == AssetVersion.id)
        .filter(AssetVersion.id == version_id)
        .first()
    )
    if row is None:
        return False
    asset, media_file = row
    if asset.asset_type != AssetType.video or media_file.s3_key_thumbnail or not media_file.s3_key_raw:
        return False

    url = generate_internal_presigned_get_url(media_file.s3_key_raw, expires_in=600)
    probe = _probe(url)
    meta = parse_probe_metadata(probe) if probe else None
    if meta is None:
        return False  # no video stream; the transcode sorts out what this file is
    stream = (probe.get("streams") or [{}])[0]
    frame = _frame(url, poster_filter(stream))
    if frame is None:
        return False

    key = poster_key(f"processed/{asset.project_id}/{asset.id}/{version_id}")
    put_object(key, frame, content_type="image/jpeg", cache_control="max-age=86400", media=True)
    width, height = display_size(meta, stream)
    # Only where nothing is recorded yet: the transcode may have got there first.
    recorded = db.execute(
        update(MediaFile)
        .where(MediaFile.id == media_file.id, MediaFile.s3_key_thumbnail.is_(None))
        .values(
            s3_key_thumbnail=key,
            width=func.coalesce(MediaFile.width, width),
            height=func.coalesce(MediaFile.height, height),
            duration_seconds=func.coalesce(MediaFile.duration_seconds, meta.duration_seconds or None),
        )
    ).rowcount
    db.commit()
    return bool(recorded)


def make_poster_in_background(version_id: uuid.UUID) -> None:
    """make_poster on its own session, for after a response has gone out."""
    db = SessionLocal()
    try:
        make_poster(db, version_id)
    except Exception:  # noqa: BLE001 - best-effort; the transcode makes the frame too
        logger.warning("early poster frame for version %s failed", version_id, exc_info=True)
        db.rollback()
    finally:
        db.close()
