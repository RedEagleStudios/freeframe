"""Playback of versions that are not ready yet, and originals a streaming project deleted.

Preview while processing: a video version that is still `processing` has no HLS
ladder yet, but its uploaded original is complete in storage. When that file is
of a type a browser can plausibly play, the stream endpoints hand out a
presigned GET of it (`kind: "progressive"`) instead of refusing. Whether the
browser can actually decode it (ProRes and HEVC usually not) is the browser's
call; the player falls back to the processing placeholder on a media error.

Streaming projects delete the original some days after a version is ready
(tasks/cleanup_tasks.py `delete_streaming_originals`); `MediaFile.raw_deleted_at`
records that, and nothing may hand the key out afterwards.
"""
from typing import Optional

from ..models.asset import Asset, AssetType, AssetVersion, MediaFile, ProcessingStatus
from .s3_service import generate_presigned_get_url

# Containers a browser has a fair chance of playing progressively. QuickTime is
# included because most .mov files are H.264 that every browser decodes; the
# ones that are not (ProRes) fail in the player, which then shows the placeholder.
PREVIEWABLE_MIME_TYPES = frozenset({"video/mp4", "video/webm", "video/quicktime"})

PREVIEW_URL_EXPIRES_IN = 3600


def original_available(media_file: MediaFile) -> bool:
    """Whether the uploaded original at `s3_key_raw` still exists."""
    return media_file.raw_deleted_at is None


def can_preview_processing(asset: Asset, version: AssetVersion, media_file: Optional[MediaFile]) -> bool:
    """Whether `version` may be watched from its original while it transcodes."""
    return (
        media_file is not None
        and asset.asset_type == AssetType.video
        and version.processing_status == ProcessingStatus.processing
        and (media_file.mime_type or "").lower() in PREVIEWABLE_MIME_TYPES
        and bool(media_file.s3_key_raw)
        and original_available(media_file)
    )


def progressive_preview_url(media_file: MediaFile) -> str:
    """A short-lived GET of the original, for a version `can_preview_processing` allowed."""
    return generate_presigned_get_url(media_file.s3_key_raw, expires_in=PREVIEW_URL_EXPIRES_IN)
