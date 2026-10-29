import uuid
import sys
import os
import asyncio
import json
import logging
from datetime import datetime, timezone

# Ensure the workspace root is on the path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from celery.exceptions import Retry

from .celery_app import celery_app
from ..database import SessionLocal
from ..models.asset import AssetVersion, MediaFile, ProcessingStatus, AssetType
from ..models.asset import Asset
from ..models.project import Project
from ..services.s3_service import get_s3_client
from ..config import settings

log = logging.getLogger("celery.transcode")


def _run_async(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@celery_app.task(bind=True, max_retries=3, default_retry_delay=60)
def process_asset(self, asset_id: str, version_id: str):
    """Main processing task dispatched after upload completes."""
    db = SessionLocal()
    try:
        version = db.query(AssetVersion).filter(AssetVersion.id == uuid.UUID(version_id)).first()
        if not version:
            return  # version already cleaned up

        asset = db.query(Asset).filter(Asset.id == uuid.UUID(asset_id)).first()
        if not asset:
            if version:
                version.processing_status = ProcessingStatus.failed
                db.commit()
            return

        media_file = db.query(MediaFile).filter(MediaFile.version_id == version.id).first()
        if not media_file:
            version.processing_status = ProcessingStatus.failed
            db.commit()
            return
        if media_file.raw_deleted_at is not None:
            # A streaming project deleted this version's original after it was
            # ready. There is nothing left to transcode from, and trying would
            # end in `failed` on a version whose ladder plays fine.
            log.warning("transcode: version %s has no original any more; leaving it as is", version_id)
            return

        # Streaming projects always keep a web MP4, whatever the instance default.
        streaming_mode = bool(
            db.query(Project.streaming_mode).filter(Project.id == asset.project_id).scalar()
        )

        # Reset to processing status before each attempt
        version.processing_status = ProcessingStatus.processing
        db.commit()

        output_prefix = f"processed/{asset.project_id}/{asset_id}/{version_id}"
        s3 = get_s3_client()

        # Whether this read of the source is the last one there will be. The
        # handler below reads this same flag to decide `failed` -- but that
        # happens after the transcode has already returned, and the transcoder
        # needs to know beforehand: a check that cannot be certain has to give
        # way on the last attempt rather than spend the master. One flag for
        # both is deliberate, so they cannot drift apart. See TranscodeJob.
        final_attempt = self.request.retries >= self.max_retries

        try:
            if asset.asset_type in (AssetType.video,):
                _process_video(
                    db, asset, version, media_file, s3, output_prefix,
                    final_attempt=final_attempt, streaming_mode=streaming_mode,
                )
            elif asset.asset_type == AssetType.audio:
                _process_audio(db, asset, version, media_file, s3, output_prefix)
            elif asset.asset_type in (AssetType.image, AssetType.image_carousel):
                _process_image(db, asset, version, media_file, s3, output_prefix)

            version.processing_status = ProcessingStatus.ready
            version.ready_at = datetime.now(timezone.utc)
            db.commit()

            # Publish SSE event (best-effort)
            _publish_event(str(asset.project_id), "transcode_complete", {
                "asset_id": asset_id,
                "version_id": version_id,
            })

        except Exception as exc:
            def _record_failure() -> None:
                from packages.transcoder.redact import redact_presigned_urls

                version.processing_status = ProcessingStatus.failed
                db.commit()
                # Every subscriber to the project's stream receives this, not
                # only the uploader, so never let a presigned URL through.
                _publish_event(str(asset.project_id), "transcode_failed", {
                    "asset_id": asset_id,
                    "version_id": version_id,
                    "error": redact_presigned_urls(str(exc)),
                })

            # `failed` is written only when nothing more is coming. Writing it
            # before every retry made the version oscillate
            # failed -> processing -> failed for the whole ladder (three retries
            # a minute apart) while its raw object sat there intact, so
            # /upload/complete's retry guard, the reaper and the client each got
            # a different answer depending on when they asked.
            if final_attempt:
                _record_failure()
                raise

            try:
                raise self.retry(exc=exc)
            except Retry:
                # Scheduled. Leave the version at `processing`: that is true, and
                # the next attempt will move it on.
                raise
            except Exception:
                # self.retry() raises Reject when the broker will not take the
                # message, so the retry never gets queued and nothing else will
                # run for this version. Leaving it at `processing` here is what
                # stranded uploads before -- record the failure we already know
                # about rather than leaving it for the sweep to find.
                _record_failure()
                raise

    finally:
        db.close()


def _process_video(
    db, asset, version, media_file, s3, output_prefix, *,
    final_attempt: bool = False, streaming_mode: bool = False,
):
    from packages.transcoder.ffmpeg_transcoder import FFmpegTranscoder, parse_qualities
    from packages.transcoder.base import TranscodeJob

    transcoder = FFmpegTranscoder(
        s3, settings.s3_bucket, settings.s3_endpoint, storage_class=settings.s3_storage_class,
    )
    # Progress is cosmetic; the transcode is not. If the broker stops answering,
    # every publish costs the socket timeout, and this runs inside the loop
    # draining ffmpeg's stdout -- so paying it ~100 times would stall the encode
    # behind a full pipe. One failure is enough to stop trying for this job.
    progress_ok = {"enabled": True, "high_water": -1}

    def _on_progress(percent: int) -> None:
        if not progress_ok["enabled"]:
            return
        # A hardware attempt that fails at runtime is retried on the software
        # backend, and the second ffmpeg starts counting from zero. Clamp so the
        # bar never runs backwards from the viewer's point of view.
        if percent <= progress_ok["high_water"]:
            return
        progress_ok["high_water"] = percent
        # The version as well as the asset: two versions of one asset can be
        # transcoding at once, and the uploads panel shows a row for each.
        delivered = _publish_event(str(asset.project_id), "transcode_progress", {
            "asset_id": str(asset.id),
            "version_id": str(version.id),
            "percent": percent,
        })
        if delivered is False:
            progress_ok["enabled"] = False

    def _on_early_meta(width, height, duration_seconds, thumbnail_key) -> None:
        # Committed at once, so the API can show (and a share link unfurl) the
        # version with a picture and a size while the ladder is still encoding.
        # The end of the transcode writes these again. Best-effort: a failed
        # write is rolled back and the transcode carries on as before.
        try:
            if thumbnail_key:
                media_file.s3_key_thumbnail = thumbnail_key
            if width:
                media_file.width = width
            if height:
                media_file.height = height
            if duration_seconds:
                media_file.duration_seconds = duration_seconds
            db.commit()
        except Exception:
            log.exception("transcode: could not record early metadata for version %s", version.id)
            db.rollback()

    job = TranscodeJob(
        media_id=str(asset.id),
        version_id=str(version.id),
        input_s3_key=media_file.s3_key_raw,
        output_s3_prefix=output_prefix,
        qualities=parse_qualities(settings.transcoder_qualities),
        progress_cb=_on_progress,
        early_meta_cb=_on_early_meta,
        final_attempt=final_attempt,
        download_mp4=settings.transcoder_download_mp4 or streaming_mode,
    )
    result = _run_async(transcoder.transcode(job))

    if result.no_video_stream:
        # The browser types a .mpg or .mp4 carrying only audio as video/*, so
        # mime_to_asset_type routed it here. The file is perfectly good, it just
        # is not video. Correct the asset and run it through the audio pipeline
        # rather than failing an upload the user had no reason to think was
        # wrong (#82).
        log.info(
            "asset %s has no video stream; re-routing to the audio pipeline",
            asset.id,
        )
        asset.asset_type = AssetType.audio
        db.flush()
        _process_audio(db, asset, version, media_file, s3, output_prefix)
        return

    if not result.success:
        raise RuntimeError(f"Transcode failed: {result.error}")

    media_file.s3_key_processed = result.hls_prefix
    # None when the remux failed. Recorded either way so a re-run of a version
    # that once had a download rung and now does not stops advertising it.
    media_file.s3_key_download = result.mp4_key
    if result.thumbnail_keys:
        media_file.s3_key_thumbnail = result.thumbnail_keys[0]
    if result.duration_seconds:
        media_file.duration_seconds = result.duration_seconds
    if result.width:
        media_file.width = result.width
    if result.height:
        media_file.height = result.height
    if result.fps:
        media_file.fps = result.fps
    db.flush()


def _process_audio(db, asset, version, media_file, s3, output_prefix):
    from packages.transcoder.image_processor import process_audio
    result = process_audio(
        s3, settings.s3_bucket, media_file.s3_key_raw, output_prefix,
        storage_class=settings.s3_storage_class,
    )
    media_file.s3_key_processed = result.get("mp3_key")
    if result.get("waveform_key"):
        media_file.s3_key_thumbnail = result["waveform_key"]
    if result.get("duration_seconds"):
        media_file.duration_seconds = result["duration_seconds"]
    db.flush()


def _process_image(db, asset, version, media_file, s3, output_prefix):
    from packages.transcoder.image_processor import process_image
    result = process_image(
        s3, settings.s3_bucket, media_file.s3_key_raw, output_prefix,
        storage_class=settings.s3_storage_class,
    )
    media_file.s3_key_processed = result.get("webp_key")
    media_file.s3_key_thumbnail = result.get("thumbnail_key")
    db.flush()


def _publish_event(project_id: str, event_type: str, payload: dict):
    """Publish an SSE event from Celery worker context.

    Delegates to event_service so there is a single emit path; it is pooled and
    already best-effort, which is what this needs.
    """
    from ..services import event_service
    return event_service.publish_sync(project_id, event_type, payload)


def _eligible_media_rows(db):
    """Rows the #124 backfill still needs: (MediaFile, asset_type) pairs."""
    return (
        db.query(MediaFile, Asset.asset_type)
        .join(AssetVersion, MediaFile.version_id == AssetVersion.id)
        .join(Asset, AssetVersion.asset_id == Asset.id)
        .filter(
            AssetVersion.processing_status == ProcessingStatus.ready,
            AssetVersion.deleted_at.is_(None),
            Asset.deleted_at.is_(None),
            MediaFile.duration_seconds.is_(None),
            # Probes read the original; one a streaming project deleted has none.
            MediaFile.raw_deleted_at.is_(None),
            Asset.asset_type.in_([AssetType.video, AssetType.audio]),
        )
        .all()
    )


@celery_app.task(bind=True)
def backfill_media_metadata(self):
    """One-off backfill for #124: probe raw S3 files to populate missing
    duration/width/height/fps on already-processed media. Idempotent —
    only touches rows where duration_seconds IS NULL."""
    import subprocess
    from packages.transcoder.ffmpeg_transcoder import (
        _PRIMARY_VIDEO_STREAM,
        display_size,
        parse_probe_metadata,
    )
    from packages.transcoder.redact import redact_presigned_urls

    db = SessionLocal()
    updated = skipped = 0
    try:
        rows = _eligible_media_rows(db)
        s3 = get_s3_client()
        for media_file, asset_type in rows:
            row_id = media_file.id
            try:
                url = s3.generate_presigned_url(
                    "get_object",
                    Params={"Bucket": settings.s3_bucket, "Key": media_file.s3_key_raw},
                    ExpiresIn=3600,
                )
                cmd = ["ffprobe", "-v", "error", "-print_format", "json", "-show_format"]
                if asset_type == AssetType.video:
                    cmd += ["-show_streams", "-select_streams", _PRIMARY_VIDEO_STREAM]
                probe = subprocess.run(cmd + [url], capture_output=True, text=True, timeout=300)
                if probe.returncode != 0:
                    skipped += 1
                    continue
                try:
                    data = json.loads(probe.stdout)
                except json.JSONDecodeError:
                    skipped += 1
                    continue
                if asset_type == AssetType.video:
                    meta = parse_probe_metadata(data)
                    if meta is None:
                        skipped += 1
                        continue
                    media_file.duration_seconds = meta.duration_seconds or None
                    media_file.width, media_file.height = display_size(meta, data["streams"][0])
                    media_file.fps = meta.fps or None
                else:
                    duration = float((data.get("format") or {}).get("duration") or 0)
                    media_file.duration_seconds = duration or None
                db.commit()
                updated += 1
            except Exception as exc:
                db.rollback()
                skipped += 1
                # A timeout quotes the probe's argv, presigned URL included.
                log.warning(
                    "backfill: skipping media_file %s: %s",
                    row_id, redact_presigned_urls(str(exc)),
                )
                continue
        return {"updated": updated, "skipped": skipped}
    finally:
        db.close()


@celery_app.task(bind=True, max_retries=2, default_retry_delay=300)
def build_download_mp4(self, version_id: str):
    """Give a ready video version the web MP4 it was transcoded without.

    Queued by the streaming-original retention task (cleanup_tasks) for a
    version in a streaming project that has no `s3_key_download` -- made before
    the project was streaming, with TRANSCODER_DOWNLOAD_MP4=false, or by the
    source-copy backend. The original is only deleted once this has succeeded.
    Remuxes the tallest rung of the ladder already in storage, with the same
    ffmpeg command the transcode uses, so the original is never read.
    """
    from packages.transcoder.ffmpeg_transcoder import FFmpegTranscoder

    db = SessionLocal()
    try:
        row = (
            db.query(MediaFile, AssetVersion)
            .join(AssetVersion, MediaFile.version_id == AssetVersion.id)
            .filter(AssetVersion.id == uuid.UUID(version_id))
            .first()
        )
        if row is None:
            return None
        media_file, version = row
        if (
            version.deleted_at is not None
            or version.processing_status != ProcessingStatus.ready
            or not media_file.s3_key_processed
            or media_file.s3_key_download
        ):
            return media_file.s3_key_download
        hls_prefix = media_file.s3_key_processed
        # Nothing is held open while ffmpeg runs; the row is re-read to record it.
        db.commit()

        transcoder = FFmpegTranscoder(
            get_s3_client(), settings.s3_bucket, settings.s3_endpoint,
            storage_class=settings.s3_storage_class,
        )
        try:
            key = transcoder.rebuild_download_mp4(hls_prefix)
        except Exception as exc:  # noqa: BLE001 - retried here, and again by the next daily run
            from packages.transcoder.redact import redact_presigned_urls
            log.warning("download-mp4: version %s: %s", version_id, redact_presigned_urls(str(exc)))
            if self.request.retries >= self.max_retries:
                return None
            raise self.retry(exc=exc)

        media_file = db.query(MediaFile).filter(MediaFile.version_id == version.id).first()
        if media_file is None or media_file.s3_key_processed != hls_prefix:
            return None  # deleted or re-transcoded meanwhile; that run owns the key
        media_file.s3_key_download = key
        db.commit()
        log.info("download-mp4: built %s for version %s", key, version_id)
        return key
    finally:
        db.close()
