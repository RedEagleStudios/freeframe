from sqlalchemy import func
from sqlalchemy.orm import Session

from ..models.asset import Asset, AssetVersion, MediaFile, ProcessingStatus
from ..models.instance_settings import InstanceSettings
from ..schemas.upload import _format_bytes, part_count_error, part_size_for, upload_size_error


def get_storage_limit(db: Session) -> int:
    """Non-creating read of the instance storage cap. 0 (or no row) == unlimited."""
    row = db.query(InstanceSettings).first()
    return row.storage_limit_bytes if row else 0


def get_upload_part_size(db: Session) -> int:
    """Non-creating read of the upload part size setting. 0 (or no row) == automatic."""
    row = db.query(InstanceSettings).first()
    return row.upload_part_size_bytes if row else 0


def get_allow_original_downloads(db: Session) -> bool:
    """Non-creating read: may people outside a project download originals? No row == no."""
    row = db.query(InstanceSettings).first()
    return bool(row.allow_original_downloads) if row else False


def choose_part_size(db: Session, file_size_bytes: int) -> tuple[int, str | None]:
    """The part size a new upload is cut on, and why it cannot be uploaded at all
    (a fixed size too small for 10,000 parts to hold the file), or None."""
    configured = get_upload_part_size(db)
    part_size = part_size_for(file_size_bytes, configured)
    return part_size, part_count_error(file_size_bytes, part_size, fixed=bool(configured))


def committed_media(db: Session, *columns):
    """Distinct `(*columns, s3_key_raw, file_size_bytes)` over *committed* media only — the
    single definition of "used storage" shared by the instance-wide and per-project sums and
    the project list.

    Counts only bytes actually in S3: versions in `processing`/`ready`, excluding soft-deleted
    assets/versions. MediaFile has no deleted_at, so soft-delete is excluded via the joins.
    Distinct on the raw key because a duplicated folder's copies name the same objects as the
    originals: one stored master is counted once however many assets show it.
    Callers add their own scope (e.g. a project filter), then sum `file_size_bytes`.
    """
    return db.query(*columns, MediaFile.s3_key_raw, MediaFile.file_size_bytes) \
        .join(AssetVersion, MediaFile.version_id == AssetVersion.id) \
        .join(Asset, AssetVersion.asset_id == Asset.id) \
        .filter(
            Asset.deleted_at.is_(None),
            AssetVersion.deleted_at.is_(None),
            AssetVersion.processing_status.in_(
                [ProcessingStatus.processing, ProcessingStatus.ready]
            ),
        ) \
        .distinct()


def _sum_bytes(db: Session, query) -> int:
    rows = query.subquery()
    return db.query(func.coalesce(func.sum(rows.c.file_size_bytes), 0)).scalar() or 0


def instance_storage_used_bytes(db: Session) -> int:
    """Instance-wide committed storage in bytes — the single source of truth for both the
    member GET indicator and cap enforcement."""
    return _sum_bytes(db, committed_media(db))


def project_storage_used_bytes(db: Session, project_id) -> int:
    """Committed storage in bytes for a single project — same semantics as the instance-wide
    figure, so per-project and instance usage agree."""
    return _sum_bytes(db, committed_media(db).filter(Asset.project_id == project_id))


def storage_cap_error(db: Session, incoming_bytes: int) -> str | None:
    """Return an error detail if accepting `incoming_bytes` would exceed the instance
    storage cap, else None. Limit 0 (unlimited) short-circuits before any usage query.
    """
    limit = get_storage_limit(db)
    if not limit:
        return None
    used = instance_storage_used_bytes(db)
    if used + incoming_bytes > limit:
        return f"Storage limit reached — {_format_bytes(used)} of {_format_bytes(limit)} used"
    return None


def upload_guard_error(db: Session, file_size_bytes: int) -> str | None:
    """Combined pre-upload guard: the per-file cap (env `MAX_UPLOAD_BYTES`) first, then the
    instance-wide storage cap (DB). Returns the first error message, or None if both pass."""
    return upload_size_error(file_size_bytes) or storage_cap_error(db, file_size_bytes)
