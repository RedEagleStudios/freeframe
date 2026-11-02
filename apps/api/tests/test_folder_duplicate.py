"""Duplicating a folder (POST /folders/{id}/duplicate).

The copy is a new subtree next to the original whose assets name the same
storage objects as the originals. Two things must hold beyond the copy itself:
the retention purge may not delete an object while another row still names it,
and storage usage counts each stored object once.
"""

import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy.orm import Session

import apps.api.routers.folders as folders_module
import apps.api.tasks.cleanup_tasks as ct
from apps.api.models.asset import (
    Asset, AssetStatus, AssetType, AssetVersion, CarouselItem, FileType, MediaFile, ProcessingStatus,
)
from apps.api.models.folder import Folder
from apps.api.models.metadata import AssetMetadata, FieldType, MetadataField
from apps.api.models.project import Project, ProjectMember, ProjectRole, ProjectType
from apps.api.models.user import User
from apps.api.schemas.folder import FolderCreate
from apps.api.services.storage import instance_storage_used_bytes, project_storage_used_bytes


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


@pytest.fixture
def project(db):
    owner = User(email=f"folder-dup-{uuid.uuid4()}@t.local", name="t")
    db.add(owner)
    db.flush()
    project = Project(name=f"folder-dup-{uuid.uuid4()}", project_type=ProjectType.personal, created_by=owner.id)
    db.add(project)
    db.flush()
    db.add(ProjectMember(project_id=project.id, user_id=owner.id, role=ProjectRole.editor))
    db.commit()
    return owner, project


def _folder(db, owner, project, name, parent_id=None):
    return folders_module.create_folder(
        project.id, FolderCreate(name=name, parent_id=parent_id), db=db, current_user=owner,
    ).id


def _asset(db, owner, project, folder_id, name, statuses=(ProcessingStatus.ready,), size=100):
    a = Asset(project_id=project.id, name=name, asset_type=AssetType.video, created_by=owner.id,
              folder_id=folder_id, status=AssetStatus.approved, keywords=["k"], rating=4)
    db.add(a)
    db.flush()
    for n, status in enumerate(statuses, start=1):
        v = AssetVersion(asset_id=a.id, version_number=n, processing_status=status, created_by=owner.id)
        db.add(v)
        db.flush()
        base = f"{project.id}/{a.id}/{v.id}"
        db.add(MediaFile(
            version_id=v.id, file_type=FileType.video, original_filename=f"{name}.mp4",
            mime_type="video/mp4", file_size_bytes=size, s3_key_raw=f"raw/{base}/original.mp4",
            s3_key_processed=f"processed/{base}", s3_key_download=f"processed/{base}/download.mp4",
            s3_key_thumbnail=f"processed/{base}/thumb.jpg", duration_seconds=12.5,
        ))
    db.commit()
    return a


def _duplicate(db, owner, folder_id):
    return folders_module.duplicate_folder(folder_id, db=db, current_user=owner)


def _copied_asset(db, folder_id):
    return db.query(Asset).filter(Asset.folder_id == folder_id).one()


def test_duplicate_copies_the_live_subtree_and_its_finished_assets(db, project):
    owner, project = project
    top = _folder(db, owner, project, "000")
    assets_dir = _folder(db, owner, project, "Assets", top)
    video = _folder(db, owner, project, "Video", assets_dir)
    trashed = _folder(db, owner, project, "Old", top)
    folders_module.delete_folder(trashed, db=db, current_user=owner)
    clip = _asset(db, owner, project, video, "clip",
                  statuses=(ProcessingStatus.ready, ProcessingStatus.failed, ProcessingStatus.ready))
    _asset(db, owner, project, top, "still-uploading", statuses=(ProcessingStatus.uploading,))
    field = MetadataField(project_id=project.id, name="Scene", field_type=FieldType.text)
    db.add(field)
    db.flush()
    db.add(AssetMetadata(asset_id=clip.id, field_id=field.id, value={"v": "12A"}))
    db.commit()

    result = _duplicate(db, owner, top)

    assert result.folder.name == "000 copy"
    assert result.folder.parent_id is None
    assert (result.folders_copied, result.assets_copied, result.assets_skipped) == (3, 1, 1)

    names = {f.name: f for f in db.query(Folder).filter(Folder.deleted_at.is_(None))
             .filter(Folder.project_id == project.id)}
    copy_assets = db.query(Folder).filter(Folder.parent_id == result.folder.id).one()
    assert copy_assets.name == "Assets"  # the trashed "Old" is not copied
    copy_video = db.query(Folder).filter(Folder.parent_id == copy_assets.id).one()
    assert copy_video.name == "Video" and "000" in names

    copy = _copied_asset(db, copy_video.id)
    assert copy.id != clip.id
    assert (copy.name, copy.rating, copy.keywords) == ("clip", 4, ["k"])
    assert copy.status == AssetStatus.draft  # review state starts over

    versions = db.query(AssetVersion).filter(AssetVersion.asset_id == copy.id).order_by(AssetVersion.version_number).all()
    assert [v.version_number for v in versions] == [1, 3]  # the failed v2 had nothing to copy
    assert all(v.processing_status == ProcessingStatus.ready for v in versions)

    source_keys = sorted(
        k for (k,) in db.query(MediaFile.s3_key_raw)
        .join(AssetVersion, MediaFile.version_id == AssetVersion.id)
        .filter(AssetVersion.asset_id == clip.id, AssetVersion.processing_status == ProcessingStatus.ready)
    )
    copy_media = db.query(MediaFile).filter(MediaFile.version_id.in_([v.id for v in versions])).all()
    assert sorted(m.s3_key_raw for m in copy_media) == source_keys
    assert all(m.duration_seconds == 12.5 for m in copy_media)

    meta = db.query(AssetMetadata).filter(AssetMetadata.asset_id == copy.id).one()
    assert meta.field_id == field.id and meta.value == {"v": "12A"}

    # The original is untouched.
    assert db.query(Asset).filter(Asset.folder_id == video).one().id == clip.id


def test_duplicate_names_the_copy_after_the_free_suffix(db, project):
    owner, project = project
    parent = _folder(db, owner, project, "Parent")
    src = _folder(db, owner, project, "Dailies", parent)

    first = _duplicate(db, owner, src)
    second = _duplicate(db, owner, src)
    copy_of_copy = _duplicate(db, owner, first.folder.id)

    assert first.folder.name == "Dailies copy"
    assert second.folder.name == "Dailies copy 2"
    assert copy_of_copy.folder.name == "Dailies copy copy"
    assert {first.folder.parent_id, second.folder.parent_id} == {parent}


def test_a_long_name_is_cut_to_fit_the_suffix(db, project):
    owner, project = project
    src = _folder(db, owner, project, "x" * 255)

    result = _duplicate(db, owner, src)

    assert len(result.folder.name) == 255
    assert result.folder.name.endswith(" copy")


def test_a_folder_inside_the_trash_cannot_be_duplicated(db, project):
    owner, project = project
    top = _folder(db, owner, project, "Top")
    inner = _folder(db, owner, project, "Inner", top)
    folders_module.delete_folder(top, db=db, current_user=owner)

    with pytest.raises(HTTPException) as exc_info:
        _duplicate(db, owner, inner)

    assert exc_info.value.status_code == 404


def test_carousel_order_points_at_the_copied_media(db, project):
    owner, project = project
    top = _folder(db, owner, project, "Stills")
    a = Asset(project_id=project.id, name="set", asset_type=AssetType.image_carousel,
              created_by=owner.id, folder_id=top)
    db.add(a)
    db.flush()
    v = AssetVersion(asset_id=a.id, version_number=1, processing_status=ProcessingStatus.ready, created_by=owner.id)
    db.add(v)
    db.flush()
    media = []
    for i in range(2):
        mf = MediaFile(version_id=v.id, file_type=FileType.image, original_filename=f"{i}.png",
                       mime_type="image/png", file_size_bytes=10, s3_key_raw=f"raw/{v.id}/{i}.png")
        db.add(mf)
        db.flush()
        media.append(mf)
        db.add(CarouselItem(version_id=v.id, media_file_id=mf.id, position=1 - i))
    db.commit()

    result = _duplicate(db, owner, top)

    copy = _copied_asset(db, result.folder.id)
    copy_version = db.query(AssetVersion).filter(AssetVersion.asset_id == copy.id).one()
    items = (
        db.query(CarouselItem.position, MediaFile.s3_key_raw)
        .join(MediaFile, CarouselItem.media_file_id == MediaFile.id)
        .filter(CarouselItem.version_id == copy_version.id)
        .order_by(CarouselItem.position)
        .all()
    )
    assert items == [(0, f"raw/{v.id}/1.png"), (1, f"raw/{v.id}/0.png")]
    assert db.query(MediaFile).filter(MediaFile.version_id == copy_version.id).count() == 2


def test_purge_keeps_objects_a_copy_still_names_and_reclaims_them_with_the_last(db, project, monkeypatch):
    deleted = []
    monkeypatch.setattr(ct, "delete_object", lambda k: deleted.append(k))
    monkeypatch.setattr(ct, "delete_prefix", lambda k: deleted.append(k))
    owner, project = project
    top = _folder(db, owner, project, "Edit")
    clip = _asset(db, owner, project, top, "clip")
    copy_id = _copied_asset(db, _duplicate(db, owner, top).folder.id).id
    keys = db.query(MediaFile).join(AssetVersion, MediaFile.version_id == AssetVersion.id) \
        .filter(AssetVersion.asset_id == clip.id).one()
    shared = {keys.s3_key_raw, keys.s3_key_processed, keys.s3_key_download, keys.s3_key_thumbnail}

    ct._purge_asset(db, clip.id, ct.PurgeCounts())
    assert deleted == []

    ct._purge_asset(db, copy_id, ct.PurgeCounts())
    assert set(deleted) == shared


def test_storage_usage_counts_a_shared_object_once(db, project):
    owner, project = project
    top = _folder(db, owner, project, "Edit")
    _asset(db, owner, project, top, "clip", size=1000)
    before_instance = instance_storage_used_bytes(db)
    assert project_storage_used_bytes(db, project.id) == 1000

    _duplicate(db, owner, top)

    assert project_storage_used_bytes(db, project.id) == 1000
    assert instance_storage_used_bytes(db) == before_instance
