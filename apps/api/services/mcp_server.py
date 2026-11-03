"""FreeFrame's MCP server: projects, files and comments as tools for AI agents.

Agents connect over Streamable HTTP at {PUBLIC_API_URL}/mcp and sign in with
OAuth (services/mcp_auth.py). Each tool acts as the signed-in user by calling
the same router function the web app calls, so access rules, soft-delete
filters and download policy are the web app's own.
"""
import math
import mimetypes
import shlex
import uuid
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Callable, Literal, Optional, TypeVar
from urllib.parse import urlparse

import anyio.from_thread
from fastapi import BackgroundTasks, HTTPException
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.routes import validate_issuer_url
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from pydantic import AnyHttpUrl, Field, ValidationError
from sqlalchemy.orm import Session
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.types import Receive, Scope, Send
from typing_extensions import Annotated

from ..config import settings
from ..models.asset import Asset, AssetVersion, MediaFile
from ..models.folder import Folder
from ..models.user import User
from ..routers import approvals as approvals_router
from ..routers import assets as assets_router
from ..routers import comments as comments_router
from ..routers import folders as folders_router
from ..routers import me as me_router
from ..routers import projects as projects_router
from ..routers import upload as upload_router
from ..schemas.approval import ApprovalCreate
from ..schemas.comment import CommentCreate
from ..schemas.upload import (
    ALLOWED_MIME_TYPES, AbortUploadRequest, CompleteUploadRequest, InitiateUploadRequest,
)
from . import oauth_service, upload_tickets
from .mcp_auth import FreeFrameOAuthProvider, SessionScope, default_session_scope
from .oauth_service import ALL_SCOPES, SCOPE_READ, SCOPE_WRITE

INSTRUCTIONS = """\
FreeFrame is a media review tool: projects hold folders of image, audio and \
video assets, each with numbered versions, timecoded comments and approvals. \
You act as the signed-in user and see what they see.

Find things with list_projects, then get_folder_tree or list_folder, or \
search_assets by name. get_asset shows an asset's versions, media details and \
download options. get_download_url returns a direct, time-limited link to the \
file itself: fetch it with `curl -L -OJ "<url>"` (the server names the file). \
Every asset carries its folder_path (from the project's top level, "" at the \
top) and a web_url to hand to a person.

add_comment and set_approval change things as the user, and need a \
connection the user allowed to make changes.

To upload a file from your machine (needs changes allowed and the editor \
role): call start_upload with its path and exact size in bytes, run the \
upload_command it returns in a shell (it sends the file straight to storage \
with curl and dd), then call finish_upload. cancel_upload throws an \
unfinished upload away.\
"""

T = TypeVar("T")

READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=False)

# Extensions the upload accepts, by the type /upload/initiate checks. The
# system's MIME table is the fallback; it differs between machines.
_MIME_BY_EXTENSION = {
    ".mp4": "video/mp4", ".m4v": "video/x-m4v", ".mov": "video/quicktime", ".qt": "video/quicktime",
    ".mkv": "video/x-matroska", ".webm": "video/webm", ".avi": "video/x-msvideo",
    ".mpg": "video/mpeg", ".mpeg": "video/mpeg", ".wmv": "video/x-ms-wmv",
    ".mp3": "audio/mpeg", ".aac": "audio/aac", ".ogg": "audio/ogg", ".wav": "audio/wav", ".bwf": "audio/wav",
    ".aif": "audio/aiff", ".aiff": "audio/aiff", ".flac": "audio/flac", ".m4a": "audio/x-m4a",
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp",
    ".heic": "image/heic", ".tif": "image/tiff", ".tiff": "image/tiff", ".gif": "image/gif",
}


def _guess_mime(file_name: str) -> Optional[str]:
    ext = ("." + file_name.rsplit(".", 1)[-1].lower()) if "." in file_name else ""
    mime = _MIME_BY_EXTENSION.get(ext) or mimetypes.guess_type(file_name)[0]
    return mime if mime in ALLOWED_MIME_TYPES else None


def _upload_command(file_path: str, parts_url: str, part_size: int, part_count: int) -> str:
    """A POSIX shell script that sends a file in the upload's parts, in order.

    Each part's presigned URL comes from the ticket endpoint just before it is
    sent (they expire after an hour), and dd cuts the part on the boundaries the
    upload was started with. The Content-Type header is dropped because the URL
    is not signed for one. Runs with the curl and dd on macOS, Linux and Git Bash."""
    return "\n".join([
        f"FILE={shlex.quote(file_path)}",
        f"PARTS={shlex.quote(parts_url)}",
        f"PART_SIZE={part_size}",
        f"COUNT={part_count}",
        "n=1",
        'while [ "$n" -le "$COUNT" ]; do',
        '  URL=$(curl -fsS --retry 3 "$PARTS/$n") || exit 1',
        '  dd if="$FILE" bs="$PART_SIZE" skip=$((n - 1)) count=1 2>/dev/null |',
        '    curl -fsS --retry 3 -X PUT -H "Content-Type:" -H "Expect:" --data-binary @- -o /dev/null "$URL" || exit 1',
        '  echo "sent part $n of $COUNT" >&2',
        "  n=$((n + 1))",
        "done",
    ])


def _run_background(tasks: BackgroundTasks) -> None:
    """Run what a router queued for after its response (the transcode dispatch),
    as FastAPI would. Tools run in a worker thread of the server's event loop."""
    anyio.from_thread.run(tasks)


def mcp_disabled_reason() -> Optional[str]:
    """Why the MCP server cannot run with this configuration, or None if it can.

    OAuth (RFC 8414) needs an HTTPS issuer; plain HTTP is allowed only on
    localhost, so an instance reached over http://<LAN address> cannot host it."""
    try:
        validate_issuer_url(AnyHttpUrl(settings.api_base_url))
    except ValueError as exc:
        return (
            f"{exc}: the MCP server needs PUBLIC_API_URL to be an https:// address "
            f"(or http://localhost), not {settings.api_base_url}"
        )
    return None


# ─── Result shapes ─────────────────────────────────────────────────────────────
# Compact on purpose: what an agent needs to choose, cite and fetch, without
# storage keys or UI-only fields.

def _project_url(project_id: Any) -> str:
    return f"{settings.frontend_url.rstrip('/')}/projects/{project_id}"


def _asset_url(project_id: Any, asset_id: Any) -> str:
    return f"{_project_url(project_id)}/assets/{asset_id}"


def _file(f: Any) -> dict:
    return {
        "original_filename": f.original_filename,
        "mime_type": f.mime_type,
        "file_type": f.file_type,
        "file_size_bytes": f.file_size_bytes,
        "width": f.width,
        "height": f.height,
        "duration_seconds": f.duration_seconds,
        "fps": f.fps,
    }


def _version(v: Any) -> dict:
    return {
        "id": str(v.id),
        "version_number": v.version_number,
        "processing_status": v.processing_status,
        "created_at": v.created_at.isoformat(),
        "files": [_file(f) for f in v.files],
    }


def _folder_paths(db: Session, project_ids: set[uuid.UUID]) -> dict[uuid.UUID, str]:
    """Every live folder's path from its project's top level ("Footage/Drone/Day 2"),
    for the given projects, in one query. A folder below a trashed one has no path."""
    if not project_ids:
        return {}
    rows = (
        db.query(Folder.id, Folder.parent_id, Folder.name)
        .filter(Folder.project_id.in_(project_ids), Folder.deleted_at.is_(None))
        .all()
    )
    folders = {row.id: row for row in rows}
    paths: dict[uuid.UUID, str] = {}
    for folder_id in folders:
        names: Optional[list[str]] = []
        current: Optional[uuid.UUID] = folder_id
        for _ in range(len(folders)):  # a parent cycle cannot loop forever
            row = folders.get(current)
            if row is None:  # an ancestor is in the trash
                names = None
                break
            names.append(row.name)
            current = row.parent_id
            if current is None:
                break
        else:
            names = None
        if names:
            paths[folder_id] = "/".join(reversed(names))
    return paths


def _asset(a: Any, folder_paths: dict[uuid.UUID, str]) -> dict:
    return {
        "id": str(a.id),
        "name": a.name,
        "asset_type": a.asset_type,
        "status": a.status,
        "project_id": str(a.project_id),
        "folder_id": str(a.folder_id) if a.folder_id else None,
        # "" at the project's top level.
        "folder_path": folder_paths.get(a.folder_id) if a.folder_id else "",
        "description": a.description,
        "rating": a.rating,
        "keywords": a.keywords or [],
        "due_date": a.due_date.isoformat() if a.due_date else None,
        "comment_count": a.comment_count,
        "created_at": a.created_at.isoformat(),
        "updated_at": a.updated_at.isoformat(),
        "latest_version": _version(a.latest_version) if a.latest_version else None,
        "thumbnail_url": a.thumbnail_url,
        "web_url": _asset_url(a.project_id, a.id),
    }


def _assets(db: Session, items: list[Any]) -> list[dict]:
    paths = _folder_paths(db, {a.project_id for a in items})
    return [_asset(a, paths) for a in items]


def _folder_node(node: Any) -> dict:
    return {
        "id": str(node.id),
        "name": node.name,
        "item_count": node.item_count,
        "children": [_folder_node(c) for c in node.children],
    }


def _comment(c: Any) -> dict:
    if c.author:
        author = c.author.name
    elif c.guest_author:
        author = f"{c.guest_author.name} (guest)"
    else:
        author = None
    return {
        "id": str(c.id),
        "version_id": str(c.version_id),
        "author": author,
        "body": c.body,
        "timecode_start": c.timecode_start,
        "timecode_end": c.timecode_end,
        "resolved": c.resolved,
        "visibility": c.visibility,
        "has_drawing": c.annotation is not None,
        "attachments": [{"file_name": a.file_name, "url": a.url} for a in c.attachments],
        "created_at": c.created_at.isoformat(),
        "replies": [_comment(r) for r in c.replies],
    }


# ─── Server ────────────────────────────────────────────────────────────────────

def create_mcp_server(session_scope: SessionScope = default_session_scope) -> MCPServer:
    server = MCPServer(
        name="freeframe",
        title="FreeFrame",
        instructions=INSTRUCTIONS,
        website_url=settings.frontend_url,
        auth_server_provider=FreeFrameOAuthProvider(session_scope),
        auth=AuthSettings(
            issuer_url=settings.api_base_url,
            resource_server_url=settings.mcp_url,
            client_registration_options=ClientRegistrationOptions(
                enabled=True, valid_scopes=ALL_SCOPES, default_scopes=ALL_SCOPES,
            ),
            revocation_options=RevocationOptions(enabled=True),
            required_scopes=[SCOPE_READ],
            # Every token this server issues is for settings.mcp_url; the
            # provider sets that itself rather than echoing the client.
            validate_token_resource=False,
        ),
    )

    def call(fn: Callable[[Session, User, list[str]], T]) -> T:
        """Run one tool as the signed-in user, with the API's errors as tool errors."""
        token = get_access_token()
        if token is None or token.subject is None:
            raise ToolError("Not signed in")
        with session_scope() as db:
            user = db.get(User, uuid.UUID(token.subject))
            if user is None:
                raise ToolError("Not signed in")
            try:
                return fn(db, user, token.scopes)
            except HTTPException as exc:
                raise ToolError(str(exc.detail)) from None
            except ValidationError as exc:
                raise ToolError(str(exc)) from None

    def require_write(scopes: list[str]) -> None:
        if SCOPE_WRITE not in scopes:
            raise ToolError(
                "This connection is read-only. To allow changes, remove it under "
                "Settings → AI access in FreeFrame and connect again with changes allowed."
            )

    def version_or_latest(db: Session, user: User, asset_id: uuid.UUID, version_id: Optional[uuid.UUID]) -> uuid.UUID:
        assets_router.get_asset(asset_id=asset_id, db=db, current_user=user)  # 404 / 403 first
        if version_id is not None:
            return version_id
        version = assets_router._display_version(db, asset_id)
        if version is None:
            raise ToolError("This asset has no version yet")
        return version.id

    @server.tool(annotations=READ_ONLY)
    def whoami() -> dict[str, Any]:
        """The FreeFrame account this connection acts as, and what it may do."""
        def run(db: Session, user: User, scopes: list[str]) -> dict:
            return {
                "id": str(user.id),
                "name": user.name,
                "email": user.email,
                "can_make_changes": SCOPE_WRITE in scopes,
                "web_url": settings.frontend_url,
            }
        return call(run)

    @server.tool(annotations=READ_ONLY)
    def list_projects() -> dict[str, Any]:
        """Every project you can open: the ones you are a member of (with your role) and public ones."""
        def run(db: Session, user: User, scopes: list[str]) -> dict:
            projects = projects_router.list_projects(db=db, current_user=user)
            return {"projects": [
                {
                    "id": str(p.id),
                    "name": p.name,
                    "description": p.description,
                    "your_role": p.role,
                    "is_public": p.is_public,
                    "asset_count": p.asset_count,
                    "member_count": p.member_count,
                    "storage_bytes": p.storage_bytes,
                    "web_url": _project_url(p.id),
                }
                for p in projects
            ]}
        return call(run)

    @server.tool(annotations=READ_ONLY)
    def get_folder_tree(project_id: uuid.UUID) -> dict[str, Any]:
        """A project's whole folder hierarchy, with how many items each folder holds."""
        def run(db: Session, user: User, scopes: list[str]) -> dict:
            tree = folders_router.get_folder_tree(project_id=project_id, db=db, current_user=user)
            return {"folders": [_folder_node(n) for n in tree]}
        return call(run)

    @server.tool(annotations=READ_ONLY)
    def list_folder(
        project_id: uuid.UUID,
        folder_id: Annotated[
            Optional[uuid.UUID], Field(description="Folder to list; omit for the project's top level")
        ] = None,
    ) -> dict[str, Any]:
        """The subfolders and assets directly inside one folder of a project."""
        def run(db: Session, user: User, scopes: list[str]) -> dict:
            where = str(folder_id) if folder_id else "root"
            subfolders = folders_router.list_folders(
                project_id=project_id, parent_id=where, db=db, current_user=user,
            )
            items = assets_router.list_assets(
                project_id=project_id, include_failed=False, folder_id=where, db=db, current_user=user,
            )
            return {
                "folders": [{"id": str(f.id), "name": f.name, "item_count": f.item_count} for f in subfolders],
                "assets": _assets(db, items),
            }
        return call(run)

    @server.tool(annotations=READ_ONLY)
    def search_assets(
        query: Annotated[str, Field(min_length=1, description="Part of the asset name")],
        limit: Annotated[int, Field(ge=1, le=100)] = 20,
    ) -> dict[str, Any]:
        """Find assets by name in the projects you are a member of and in files shared with you.

        Public projects you have not joined are not searched; browse them with list_folder."""
        def run(db: Session, user: User, scopes: list[str]) -> dict:
            found = me_router.list_my_assets(filter=None, q=query, skip=0, limit=limit, db=db, current_user=user)
            return {"assets": _assets(db, found)}
        return call(run)

    @server.tool(annotations=READ_ONLY)
    def get_asset(asset_id: uuid.UUID) -> dict[str, Any]:
        """One asset in full: every version with its media details, and the download options for the latest."""
        def run(db: Session, user: User, scopes: list[str]) -> dict:
            asset = assets_router.get_asset(asset_id=asset_id, db=db, current_user=user)
            versions = assets_router.list_asset_versions(asset_id=asset_id, db=db, current_user=user)
            try:
                options = assets_router.list_download_options(
                    asset_id=asset_id, version_id=None, db=db, current_user=user,
                )
            except HTTPException:
                options = []  # nothing ready to download yet
            return {
                **_assets(db, [asset])[0],
                "versions": [_version(v) for v in versions],
                "download_options": [o.model_dump() for o in options],
            }
        return call(run)

    @server.tool(annotations=READ_ONLY)
    def get_download_url(
        asset_id: uuid.UUID,
        version_id: Annotated[
            Optional[uuid.UUID], Field(description="Version to download; omit for the latest ready one")
        ] = None,
        rendition: Annotated[
            Optional[str],
            Field(description="An id from get_asset's download_options, such as 'original' or 'transcode'. "
                              "Omit for the original upload when you may download it, else the transcode."),
        ] = None,
    ) -> dict[str, Any]:
        """A direct, time-limited download link for an asset's file.

        Fetch it with `curl -L -OJ "<url>"`; the link names the file. It expires after expires_in seconds."""
        def run(db: Session, user: User, scopes: list[str]) -> dict:
            options = assets_router.list_download_options(
                asset_id=asset_id, version_id=version_id, db=db, current_user=user,
            )
            by_id = {o.id: o for o in options}
            if rendition is not None and rendition not in by_id:
                raise ToolError(
                    f"No download option {rendition!r} for this version; available: {', '.join(by_id) or 'none'}"
                )
            chosen = by_id[rendition] if rendition else by_id.get("original") or (options[0] if options else None)
            if chosen is None:
                raise ToolError("This version has nothing to download")
            link = assets_router.get_stream_url(
                asset_id=asset_id, version_id=version_id, download=True, rendition=chosen.id,
                prefer_original=False, db=db, current_user=user,
            )
            return {
                "url": link.url,
                "expires_in": link.expires_in,
                "rendition": chosen.id,
                "label": chosen.label,
                "format": chosen.format,
                "size_bytes": chosen.size_bytes,
            }
        return call(run)

    @server.tool(annotations=READ_ONLY)
    def list_comments(
        asset_id: uuid.UUID,
        version_id: Annotated[
            Optional[uuid.UUID], Field(description="Only this version's comments; omit for every version")
        ] = None,
    ) -> dict[str, Any]:
        """An asset's comment threads, oldest first. Timecodes are seconds into the media."""
        def run(db: Session, user: User, scopes: list[str]) -> dict:
            threads = comments_router.list_comments(
                asset_id=asset_id, version_id=version_id, visibility=None, db=db, current_user=user,
            )
            return {"comments": [_comment(c) for c in threads]}
        return call(run)

    @server.tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=False))
    def add_comment(
        asset_id: uuid.UUID,
        body: Annotated[str, Field(min_length=1)],
        version_id: Annotated[
            Optional[uuid.UUID], Field(description="Version to comment on; omit for the latest")
        ] = None,
        timecode_start: Annotated[Optional[float], Field(ge=0, description="Seconds into the media")] = None,
        timecode_end: Annotated[Optional[float], Field(ge=0, description="Seconds; makes a range comment")] = None,
        reply_to: Annotated[
            Optional[uuid.UUID], Field(description="Comment id to reply to (the reply joins its version)")
        ] = None,
        internal: Annotated[bool, Field(description="Visible to project members only, not on share links")] = False,
    ) -> dict[str, Any]:
        """Post a comment on an asset as you, optionally at a timecode or as a reply. Notifies like a web comment."""
        def run(db: Session, user: User, scopes: list[str]) -> dict:
            require_write(scopes)
            # A reply joins its parent's version; reply_to_comment sets that itself.
            request = CommentCreate(
                version_id=version_or_latest(db, user, asset_id, version_id),
                body=body,
                timecode_start=timecode_start,
                timecode_end=timecode_end,
                visibility="internal" if internal else "public",
            )
            if reply_to is not None:
                created = comments_router.reply_to_comment(
                    asset_id=asset_id, comment_id=reply_to, body=request, db=db, current_user=user,
                )
            else:
                created = comments_router.create_comment(asset_id=asset_id, body=request, db=db, current_user=user)
            return _comment(created)
        return call(run)

    @server.tool(annotations=ToolAnnotations(
        read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=False,
    ))
    def set_approval(
        asset_id: uuid.UUID,
        decision: Literal["approved", "rejected"],
        note: Optional[str] = None,
        version_id: Annotated[
            Optional[uuid.UUID], Field(description="Version to decide on; omit for the latest")
        ] = None,
    ) -> dict[str, Any]:
        """Approve or reject an asset version as you. Needs the reviewer role or higher in its project."""
        def run(db: Session, user: User, scopes: list[str]) -> dict:
            require_write(scopes)
            request = ApprovalCreate(version_id=version_or_latest(db, user, asset_id, version_id), note=note)
            decide = approvals_router.approve_asset if decision == "approved" else approvals_router.reject_asset
            approval = decide(asset_id=asset_id, body=request, db=db, current_user=user)
            return {
                "asset_id": str(approval.asset_id),
                "version_id": str(approval.version_id),
                "status": approval.status,
                "note": approval.note,
            }
        return call(run)

    def own_upload(db: Session, user: User, version_id: uuid.UUID) -> tuple[AssetVersion, MediaFile]:
        version = db.query(AssetVersion).filter(
            AssetVersion.id == version_id, AssetVersion.deleted_at.is_(None),
        ).first()
        media_file = db.query(MediaFile).filter(MediaFile.version_id == version_id).first()
        if version is None or media_file is None or version.created_by != user.id:
            raise ToolError("No upload of yours with that version_id")
        return version, media_file

    @server.tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=False))
    def start_upload(
        project_id: uuid.UUID,
        file_path: Annotated[str, Field(
            min_length=1, description="The file's path on the machine that will run upload_command",
        )],
        file_size_bytes: Annotated[int, Field(gt=0, description="Its exact size in bytes, e.g. from `wc -c < FILE`")],
        folder_id: Annotated[
            Optional[uuid.UUID], Field(description="Folder for a new asset; omit for the project's top level")
        ] = None,
        asset_id: Annotated[
            Optional[uuid.UUID], Field(description="Upload as the next version of this asset instead of a new asset")
        ] = None,
        name: Annotated[Optional[str], Field(description="Name for a new asset; defaults to the file name")] = None,
        mime_type: Annotated[
            Optional[str], Field(description="Only if the file's extension does not say what it is, e.g. video/mp4")
        ] = None,
    ) -> dict[str, Any]:
        """Start uploading a video, audio or image file from your machine, as a new asset or a new version.

        Returns upload_command, a shell script that sends the file straight to storage in parts with curl and
        dd (the file never passes through this connection). Run it in a shell that can read file_path, then call
        finish_upload with the version_id. The command works for 24 hours. Needs the editor role or higher."""
        def run(db: Session, user: User, scopes: list[str]) -> dict:
            require_write(scopes)
            token = get_access_token()
            grant = oauth_service.grant_for_access_token(db, token.token) if token else None
            if grant is None:
                raise ToolError("Not signed in")
            file_name = file_path.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
            mime = mime_type or _guess_mime(file_name)
            if mime not in ALLOWED_MIME_TYPES:
                raise ToolError(
                    f"FreeFrame does not take {mime or file_name!r}. Pass mime_type, one of: "
                    + ", ".join(sorted(ALLOWED_MIME_TYPES))
                )
            started = upload_router.initiate_upload(
                InitiateUploadRequest(
                    project_id=project_id,
                    asset_name=name or file_name,
                    original_filename=file_name,
                    mime_type=mime,
                    file_size_bytes=file_size_bytes,
                    asset_id=asset_id,
                    folder_id=folder_id,
                ),
                db=db,
                current_user=user,
            )
            ticket, expires_at = upload_tickets.create_ticket(user.id, started.version_id, grant.id)
            part_count = math.ceil(file_size_bytes / started.chunk_size_bytes)
            return {
                "asset_id": str(started.asset_id),
                "version_id": str(started.version_id),
                "part_size_bytes": started.chunk_size_bytes,
                "part_count": part_count,
                "expires_at": expires_at.isoformat(),
                "upload_command": _upload_command(
                    file_path,
                    f"{settings.api_base_url}/upload/agent/{ticket}/parts",
                    started.chunk_size_bytes,
                    part_count,
                ),
                "next_step": "Run upload_command in a shell, then call finish_upload with this version_id.",
            }
        return call(run)

    @server.tool(annotations=ToolAnnotations(
        read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=False,
    ))
    def finish_upload(version_id: uuid.UUID) -> dict[str, Any]:
        """Finish an upload once upload_command has sent every part: the file is checked and its processing starts.

        If parts are missing it says which; run upload_command again and call this again."""
        def run(db: Session, user: User, scopes: list[str]) -> dict:
            require_write(scopes)
            version, media_file = own_upload(db, user, version_id)
            background = BackgroundTasks()
            done = upload_router.complete_upload(
                CompleteUploadRequest(
                    s3_key=media_file.s3_key_raw,
                    upload_id=version.upload_id or "",
                    asset_id=version.asset_id,
                    version_id=version.id,
                ),
                background_tasks=background,
                db=db,
                current_user=user,
            )
            _run_background(background)
            asset = db.get(Asset, done.asset_id)
            return {
                "asset_id": str(done.asset_id),
                "version_id": str(done.version_id),
                "status": done.status,
                "web_url": _asset_url(asset.project_id, asset.id),
            }
        return call(run)

    @server.tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=True, open_world_hint=False))
    def cancel_upload(version_id: uuid.UUID) -> dict[str, Any]:
        """Throw away an upload that has not been finished: its sent parts are deleted, and so is the asset
        if this was its only version."""
        def run(db: Session, user: User, scopes: list[str]) -> dict:
            require_write(scopes)
            version, media_file = own_upload(db, user, version_id)
            background = BackgroundTasks()
            upload_router.abort_upload(
                AbortUploadRequest(
                    s3_key=media_file.s3_key_raw,
                    upload_id=version.upload_id or "",
                    version_id=version.id,
                    discard=True,
                ),
                background_tasks=background,
                db=db,
                current_user=user,
            )
            _run_background(background)
            return {"version_id": str(version_id), "cancelled": True}
        return call(run)

    return server


AS_METADATA_PATH = "/.well-known/oauth-authorization-server"


def create_mcp_app(session_scope: SessionScope = default_session_scope) -> Starlette:
    """The MCP endpoint plus the OAuth endpoints and metadata, as one ASGI app."""
    app = create_mcp_server(session_scope).streamable_http_app(
        streamable_http_path="/mcp",
        # Stateless: production runs several API workers, and the 2026-07-28
        # protocol has no sessions to pin a client to one of them.
        stateless_http=True,
        json_response=True,
        # Rebinding protection guards unauthenticated local servers. This one is
        # public behind a proxy, and every call needs a bearer token.
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )
    # The SDK serves the issuer's metadata at the bare well-known path. With an
    # issuer that has a path (https://host/api), clients ask for
    # /.well-known/oauth-authorization-server/api (RFC 8414 §3.1), so a proxy
    # can pass origin-root /.well-known/oauth-* to the API unchanged.
    issuer_path = urlparse(settings.api_base_url).path.rstrip("/")
    if issuer_path:
        metadata = next(r for r in app.routes if getattr(r, "path", None) == AS_METADATA_PATH)
        app.router.routes.append(
            Route(AS_METADATA_PATH + issuer_path, endpoint=metadata.endpoint, methods=metadata.methods)
        )
    return app


class McpEndpoint:
    """The ASGI app main.py routes the MCP and OAuth paths to.

    A session manager runs once per instance, so each application lifespan
    (each worker start, each test client) gets a freshly built app."""

    def __init__(self, session_scope: SessionScope = default_session_scope):
        self._session_scope = session_scope
        self.app = create_mcp_app(session_scope)
        self.paths = [route.path for route in self.app.routes]

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        await self.app(scope, receive, send)

    @asynccontextmanager
    async def running(self) -> AsyncIterator[None]:
        app = create_mcp_app(self._session_scope)
        async with app.router.lifespan_context(app):
            self.app = app
            yield
