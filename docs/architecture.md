# Architecture Overview

This document explains how FreeFrame's components work together.

---

## System Overview

FreeFrame is a monorepo with two main applications and supporting infrastructure:

```
                         ┌──────────────┐
           Users ──────▶ │   Traefik    │
                         │   :80/:443   │
                         └──────┬───────┘
                                │
                    ┌───────────┴───────────┐
                    ▼                       ▼
             ┌─────────────┐        ┌─────────────┐
             │   Next.js    │        │   FastAPI    │──── SSE ──▶ Clients
             │   Frontend   │        │   Backend    │
             └─────────────┘        └──────┬───────┘
                                           │
                     ┌─────────────────────┼────────────────────┐
                     ▼                     ▼                    ▼
              ┌───────────┐         ┌───────────┐       ┌──────────────┐
              │ PostgreSQL │         │   Redis    │       │  S3 Storage   │
              │            │         │           │       │              │
              └───────────┘         └─────┬─────┘       └──────────────┘
                                          │
                               ┌──────────┴──────────┐
                               ▼                     ▼
                        ┌─────────────┐       ┌─────────────┐
                        │  Transcoding │       │    Email     │
                        │   Workers    │       │   Workers    │
                        └─────────────┘       └─────────────┘
```

| Component | Role |
|-----------|------|
| **Traefik** | Reverse proxy, opt-in SSL via Let's Encrypt (`docker-compose.https.yml`), routes `/api/*` to backend and `/` to frontend |
| **Next.js** | Server-rendered frontend, handles UI, auth cookies, client-side media playback |
| **FastAPI** | REST API, auth, business logic, SSE events, S3 presigned URLs |
| **PostgreSQL** | Primary datastore for all entities (users, projects, assets, comments, etc.) |
| **Redis** | Message broker for Celery task queues, magic code TTL storage |
| **S3 Storage** | Stores all media files (originals, transcoded outputs, thumbnails) |
| **Transcoding Workers** | Celery workers that process video/audio/image files via FFmpeg |
| **Email Workers** | Celery workers that send transactional emails (invites, magic codes, notifications) |
| **Maintenance Worker** | Celery worker for scheduled housekeeping: retention GC, stale-upload reaper, orphan sweep |
| **Beat** | Celery scheduler that publishes the periodic maintenance tasks |

---

## Data Flow

### Upload and Processing

```
User uploads file
    │
    ▼
Frontend initiates multipart upload
    │
    ▼
API creates presigned URLs ──▶ Frontend uploads chunks directly to S3
    │
    ▼
Frontend calls /upload/complete
    │
    ▼
API dispatches Celery task ──▶ Worker downloads from S3
                                   │
                                   ▼
                               FFmpeg processes file
                                   │
                                   ▼
                               Worker uploads outputs to S3
                                   │
                                   ▼
Frontend receives  ◀────────── SSE: transcode_complete
transcode_complete             (or transcode_failed)
```

Each upload is cut into parts of one size, chosen at initiate from the instance's part size
setting (automatic sizing aims for about 500 parts) and pinned to the version, so a resume
and a later change to the setting never disagree about where a part's bytes start.

A transfer that stops part-way leaves the multipart upload open and its parts in
the bucket. `AbortMultipartUpload` is only ever called for an explicit user
cancel (or by the stale-upload reaper), so the version stays `uploading` and the
panel offers to resume it. `GET /upload/{version_id}/parts` reports which parts
storage already holds — matched on size, not ETag, which is not portable — plus
the key, the upload id and the chunk size that upload was pinned to, and the
client sends only what is missing. The browser cannot reopen a local file on its
own, so resuming asks the user to select it again.

### Review and Approval

```
Reviewer opens asset
    │
    ▼
Frontend loads HLS stream (video) / WebP (image) / MP3 (audio)
    │
    ▼
Reviewer adds comment (with optional timecode + drawing annotation)
    │
    ▼
API saves comment ──▶ SSE: new_comment ──▶ Other viewers see it live
    │
    ▼
Reviewer approves / rejects ──▶ SSE: approval_updated
```

---

## Media Processing Pipeline

### Video

1. Raw file uploaded to S3 via presigned multipart upload
2. Celery worker reads directly from S3 presigned URL (no full download)
3. `ffprobe` extracts metadata (duration, resolution, FPS)
4. FFmpeg generates multi-bitrate HLS:
   - 1080p (CRF 20), 720p (CRF 22), 360p (CRF 26) by default; 2160p (CRF 20) is available
     through `TRANSCODER_QUALITIES` but not built unless configured
   - 2-second segments with forced keyframes
   - The ladder is trimmed against the source resolution, so a small source never gets
     upscaled renditions. When the source falls between rungs, or under all of them, the
     next rung up is built at the source's own size, so the top rendition matches the source.
   - An optional NVENC/VAAPI hardware backend is selected at runtime when available, using
     `-global_quality` in place of `-crf`, and falls back to software on failure. A source
     the GPU can't decode (ProRes, 4:2:2, 10-bit H.264) is decoded on the CPU and uploaded,
     so the scale and encode stay on the GPU. HDR and
     Dolby Vision sources are tone-mapped to Rec.709, with the CRF adjusted either way.
5. One thumbnail generated (`-vf thumbnail`, a single representative frame), scaled to square
   pixels so anamorphic sources aren't squeezed, and fit within 1280x1280 keeping its shape
6. All outputs uploaded to S3 under `processed/{project_id}/{asset_id}/{version_id}/`
7. `AssetVersion.processing_status` set to `ready`, `transcode_complete` published

No waveform is produced for video; waveforms are an audio-pipeline output only.

### Audio

1. Raw file (MP3, WAV, FLAC, AAC) uploaded to S3
2. Worker normalizes audio and converts to MP3
3. Waveform JSON generated for visualization
4. Outputs uploaded to S3

### Image

1. Raw file (JPEG, PNG, HEIC, TIFF) uploaded to S3
2. Worker converts to optimized WebP + generates thumbnail

> The `image_carousel` asset type exists in the enum but is not implemented: no upload path
> produces it, each version holds exactly one `MediaFile`, and `CarouselItem` rows are never
> created.

---

## Permission Model

FreeFrame is **single-tenant**: one deployment is one workspace. There is no organization or
team layer. `Project` is the root of the content hierarchy, and all content permissions are
project-scoped or share-scoped.

```
Project
├── owner    ── full control over project
├── editor   ── upload, edit assets
├── reviewer ── comment, approve/reject
└── viewer   ── read-only, and may still comment

Share Link (unauthenticated access, by token)
├── approve  ── may approve/reject
├── comment  ── may add comments
└── view     ── read-only
```

`User.is_superadmin` is a separate, instance-level flag covering user administration:
invite, deactivate, role change, first-run setup. It grants **no** content access and is not
consulted when resolving asset permissions. A superadmin may list every live project
(`GET /projects?scope=all`, with each project's owners) and join any of them as owner
(`POST /admin/projects/{id}/join`), which is how a private project whose owners are gone is
recovered. Joining adds (or revives, or upgrades) a real `ProjectMember` row with role `owner`,
so the admin gets access through the ordinary member checks and appears in the project's People
list; until they join, they have none.

**Asset access** is resolved by `can_access_asset` in `apps/api/services/permissions.py`, which
returns true on the first of these that matches:

1. The user created the asset (`Asset.created_by`)
2. The user is a `ProjectMember` in any role
3. The asset is shared directly with the user (`AssetShare.shared_with_user_id`)
4. The project is public (`Project.is_public`) — any authenticated user may reach it

Anything outside those four paths requires a `ShareLink` token. A share link targets an asset,
a folder, a project, or an explicit set of items, and may be password-protected; folder-scoped
links apply transitively to the folder's contents. Guests commenting through a share link are
recorded in the `GuestUser` table with email and name only, no account required.

**Everything requires a live project.** A deleted project (one with `deleted_at` set) is treated as gone on every path: `require_project_role` and the project listings answer 404 *Project not found*, `can_access_asset` refuses its assets, and a share link resolves only while its project, its asset or folder, and every folder above that are live, answering 404 *Share link not found* like an unknown token otherwise. Deleting a folder or asset leaves its share links in place, so restoring it brings them back. See [GHSA-w8vh-2j74-m3hw](https://github.com/Techiebutler/freeframe/security/advisories/GHSA-w8vh-2j74-m3hw).

> **Note:** `AssetShare.shared_with_team_id` is still written by the two `share/team` endpoints
> but is never read when resolving access, so a team share currently grants access to nobody.
> Tracked separately; do not rely on it.

---

## Real-Time Updates (SSE)

FreeFrame uses **Server-Sent Events** (not WebSockets) for real-time updates. A single SSE
endpoint per project streams all events:

```
GET /events/{project_id}
```

`routers/events.py` serves the stream; `services/event_service.py` subscribes to the Redis
channel `project:{project_id}`, and anything published there reaches the browser.

| Event | Payload | When |
|-------|---------|------|
| `transcode_progress` | `{asset_id, percent}` | Whole-percent advances during video processing |
| `transcode_complete` | `{asset_id, version_id}` | Processing finished |
| `transcode_failed` | `{asset_id, error}` | Processing failed |
| `new_comment` | `{asset_id, comment_id, author}` | Comment posted, including replies and guest comments |
| `comment_resolved` | `{comment_id, resolved}` | Comment resolved or unresolved (the endpoint toggles) |
| `approval_updated` | `{asset_id, user_id, status}` | Approval status changed |

All six are published through `event_service.publish_sync`, which is the single emit path. It
is best-effort: every call site publishes after its commit, and a broker that is down or slow
degrades to "no live update" rather than failing the request that produced it.

Access is checked when a stream connects and again for as long as it stays open, with the same
rule both times: an active, undeleted account, an unexpired access token whose `ver` matches
`users.token_version`, and a live project the user is a member of or that is public. An open
stream re-checks before it delivers an event once its last check is older than
`SSE_AUTH_MAX_AGE_SECONDS` (default 1), and every `SSE_AUTH_RECHECK_SECONDS` (default 15) while
idle. Token expiry is exact rather than gated: the token's `exp` is compared before every write,
and an idle stream wakes at `exp` to close. An idle stream is sent a keepalive at the recheck
interval or every 30 seconds, whichever is shorter. Each check is one indexed query on its own
short-lived session, so an open stream holds no database connection. When a check fails, the
stream sends a final `revoked` event with `{reason}` (`access_revoked`, `token_expired` or
`token_revoked`) and closes; a connection that fails the check is refused with 403.

Clients reconnect automatically on disconnect. After `revoked`, `hooks/use-sse.ts` refreshes the
token and reconnects for the two token reasons, and stops for `access_revoked`. SSE was chosen
over WebSockets because it's simpler, works through most proxies, and is sufficient for an async
review workflow.

---

## AI Access (MCP)

AI assistants (Claude, Cursor, VS Code and any other MCP client) connect to the API's MCP
server over Streamable HTTP at `{PUBLIC_API_URL}/mcp`. `services/mcp_server.py` builds it with
the official MCP Python SDK, stateless so any API worker can answer any request.

**Sign-in is OAuth 2.1, with FreeFrame as its own authorization server.** There are no API keys.

```
AI app ── POST /mcp ───────────────────────────────▶ 401 + WWW-Authenticate (resource metadata)
       ── GET /.well-known/oauth-protected-resource/… and /.well-known/oauth-authorization-server
       ── POST /register   (Dynamic Client Registration)
       ── browser → GET /authorize ─▶ web /oauth/consent?request=…  (user signs in, approves)
       ◀─ redirect_uri?code=…&state=…&iss=…
       ── POST /token      (code + PKCE verifier) ─▶ access token (1 h) + refresh token (90 days)
       ── POST /mcp with Bearer token ─▶ tools
```

The SDK runs the protocol endpoints and checks clients, redirect URIs, PKCE and expiry;
`services/mcp_auth.py` supplies storage and policy, backed by `services/oauth_service.py`.
Parked consent requests (10 minutes) and one-time codes (5 minutes) live in Redis. Registered
clients (`oauth_clients`) and each user's approval of a client (`oauth_grants`) live in
Postgres. A grant stores only SHA-256 hashes of its current access and refresh token. Every
refresh replaces both tokens with a compare-and-set, so a refresh token works once even when two
refreshes race, and renews the refresh token's lifetime. A connection in use therefore never
expires, and an unused one lapses after `MCP_REFRESH_TOKEN_EXPIRE_DAYS`. A grant ends when the
user disconnects it (Settings → AI access, `DELETE /oauth/grants/{id}`), when the client
revokes it, and when the account is deleted, deactivated or changes its password
(`token_version`).

Scopes: `freeframe:read` (browse, download, read comments) is always granted; `freeframe:write`
(upload, post comments, approve or reject) only if the user leaves "Allow changes" on at consent. The
consent page decides because clients request what the protected-resource metadata advertises.

**Tools act as the user through the web app's own code.** Each tool calls the router function
the matching web request would reach (`list_projects`, `list_assets`, `get_stream_url`,
`create_comment`, …) with the token's user, so the permission model above, the `deleted_at`
filters and the download policy apply unchanged. `get_download_url` returns the same presigned
link the web app's download button gets.

**Uploads keep the file out of the MCP connection.** `start_upload` runs the browser's
`/upload/initiate` and returns a shell script (`curl` and `dd`) plus an upload ticket, a JWT
(`services/upload_tickets.py`, 24 hours) naming the version, the user and the OAuth grant. The
script asks `GET /upload/agent/{ticket}/parts/{n}` for each part's presigned URL just before
sending it, and PUTs the part straight to storage on the boundaries the upload was started with.
That endpoint checks the grant is still live and allowed to make changes on every part, so
disconnecting the app stops an upload in progress, then runs the browser's presign-part.
`finish_upload` and `cancel_upload` are `/upload/complete` and `/upload/abort` with discard.

Discovery lives at the origin root, which the production proxy hands to the web app, so
`app/.well-known/oauth-*` route handlers pass those requests to the API (`lib/oauth-metadata.ts`).
OAuth requires an HTTPS issuer (plain HTTP only on localhost); with any other `PUBLIC_API_URL`
the MCP server stays off and the API logs why.

---

## Database

**Soft delete is common but not universal.** 13 of the 34 models carry a `deleted_at` column;
21 do not, including `MediaFile`, `Annotation`, `CommentAttachment`, `CommentReaction`,
`Notification`, `ActivityLog` and `ProjectLink`. Nothing filters the column automatically — every query does
it by hand. Soft deletion is also not permanent: `tasks/cleanup_tasks.py` hard-deletes
soft-deleted rows and reclaims their S3 objects after `SOFT_DELETE_RETENTION_DAYS`
(default 30).

**A storage object can belong to several `MediaFile` rows.** Duplicating a folder
(`POST /folders/{id}/duplicate`) gives the copied versions the originals' S3 keys instead of
copying the bytes. The purge deletes an object only when no other media row still names it
(`_reclaim_media_s3`), and storage usage (`services/storage.committed_media`) counts each raw
object once. Only `ready` versions are copied, so discarding an upload and the stale-upload
reaper never meet a shared key. Any other code that deletes media objects needs the same check.

Key entity relationships:

```
Projects ──── ProjectMembers
    │
    ├── Folders (nested, max depth 10) ──┬── Assets
    │                                    └── ProjectLinks (outside URLs shown in the tree)
    │
    ├── Assets ──┬── AssetVersions ──── MediaFiles
    │            ├── Comments ──┬── Annotations
    │            │              ├── Attachments
    │            │              └── Reactions
    │            ├── Approvals
    │            ├── AssetMetadata ──── MetadataFields
    │            └── AssetShares
    │
    ├── Collections ──── CollectionShares
    ├── ProjectBranding / WatermarkSettings
    └── ShareLinks ──┬── ShareLinkItems
                     └── ShareLinkActivity

Users (instance-wide; User.is_superadmin) · GuestUsers (share-link commenters)
InstanceBranding · InstanceSettings   (single-row instance configuration)
```

The `organizations`, `org_members`, `teams` and `team_members` tables still exist in any
migrated database. They are created by the initial migration and were never dropped, but they
have no ORM model and no route behind them. Ignore them.

**ORM:** SQLAlchemy 2.0 with Alembic for migrations.
