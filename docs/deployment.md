# Production Deployment Guide

This guide covers deploying FreeFrame to a production server using Docker Compose.

---

## Hardware Requirements

| | Minimum | Recommended | Heavy Workload |
|---|---|---|---|
| **CPU** | 2 cores | 4 cores | 8+ cores |
| **RAM** | 4 GB | 8 GB | 16+ GB |
| **Storage** | 20 GB SSD | 50 GB SSD | 100+ GB NVMe |
| **Bandwidth** | 100 Mbps | 500 Mbps | 1 Gbps |

- **Minimum** — Small teams (up to 10 users), light video transcoding
- **Recommended** — Medium teams (10-50 users), regular video uploads
- **Heavy Workload** — Large teams (50+ users), frequent 4K video processing

> **Note:** Video transcoding is the most resource-intensive operation. Storage needs depend on your media volume — the actual media files are stored in S3, not on the server.

---

## Deployment Options

FreeFrame runs anywhere Docker is available. Here are common hosting options:

### VPS / Cloud VM (Simplest)

Best for most teams. A single server running Docker Compose.

| Provider | Recommended Plan | Est. Cost/mo |
|----------|-----------------|--------------|
| **Hetzner** | CPX31 (4 vCPU, 8 GB) | ~$15 |
| **DigitalOcean** | Droplet (4 vCPU, 8 GB) | ~$48 |
| **AWS EC2** | t3.medium (4 vCPU, 8 GB) | ~$60 |
| **Google Cloud** | e2-custom (4 vCPU, 8 GB) | ~$75 |
| **Azure** | B4s (4 vCPU, 8 GB) | ~$70 |
| **Hostinger VPS** | KVM 4 (4 vCPU, 16 GB) | ~$16 |

**Setup:** SSH into your server, install Docker, clone the repo, and follow the [Quick Setup](#quick-setup) below.

### Cloud with Managed Services

For teams that want managed databases and less maintenance. Use external PostgreSQL, Redis, and S3 instead of the Docker-included ones. See [Bring Your Own Infrastructure](#bring-your-own-infrastructure).

| Component | AWS | GCP | Azure |
|-----------|-----|-----|-------|
| Server | EC2 / ECS | Compute Engine / Cloud Run | VM / Container Apps |
| Database | RDS PostgreSQL | Cloud SQL | Azure Database for PostgreSQL |
| Redis | ElastiCache | Memorystore | Azure Cache for Redis |
| Storage | S3 | Cloud Storage | Blob Storage (via S3 API) |
| Email | SES | (use SMTP) | (use SMTP) |

### Bare Metal / On-Premise

FreeFrame is fully self-contained. Install Docker on any Linux server (Ubuntu 22.04+ recommended) and follow the Quick Setup. Ideal for organizations that require media to stay on their own hardware.

---

## Prerequisites

- A server meeting the [hardware requirements](#hardware-requirements) with **Docker** and **Docker Compose** installed
- A **domain name** pointed to your server's IP (for SSL — optional for testing)
- An **S3-compatible storage** bucket (AWS S3, Cloudflare R2, Backblaze B2, etc.)
- An **SMTP server** or AWS SES for sending emails

## Quick Setup

```bash
# 1. Clone the repository
git clone https://github.com/Techiebutler/freeframe.git
cd freeframe

# 2. Create your production environment file
cp .env.example .env.prod

# 3. Edit .env.prod with your actual credentials
#    At minimum: change passwords, set FRONTEND_URL to your https:// URL, configure S3, email, and JWT_SECRET
nano .env.prod

# 4. Build and start all services
docker compose --env-file .env.prod -f docker-compose.prod.yml up -d --build

# 5. Check that everything is running
docker compose --env-file .env.prod -f docker-compose.prod.yml ps
```

FreeFrame is now running on **port 80**. The first user to sign up becomes the super admin via the setup wizard.

---

## SSL / TLS Setup

FreeFrame uses **Traefik** as its reverse proxy. HTTPS with **Let's Encrypt** certificates is opt-in: add `docker-compose.https.yml` to the command. `docker-compose.prod.yml` on its own serves plain HTTP on port 80 and never requests a certificate, even if `DOMAIN` and `ACME_EMAIL` are set.

### Enabling SSL

Set these variables in your `.env.prod`:

```
DOMAIN=your-domain.com
ACME_EMAIL=admin@your-domain.com
FRONTEND_URL=https://your-domain.com
```

Then start (or restart) the services with both compose files:

```bash
docker compose --env-file .env.prod -f docker-compose.prod.yml -f docker-compose.https.yml up -d --build
```

Use the same two `-f` flags for every later `docker compose` command (`ps`, `logs`, `down`). Traefik will:
- Request an SSL certificate from Let's Encrypt for `DOMAIN`
- Serve your site over HTTPS on port 443
- Redirect plain HTTP on port 80 to HTTPS
- Auto-renew the certificate before it expires

`docker compose` refuses to start if `DOMAIN` or `ACME_EMAIL` is empty when the HTTPS file is used.

With the HTTPS file, every plain-HTTP request is redirected to HTTPS, so opening the server by its IP address or another hostname lands on HTTPS with a certificate warning. Use the domain.

> **Requirements:** Your domain's DNS A record must point to your server, and ports 80 + 443 must be open. Traefik needs port 80 for the ACME HTTP challenge even when serving HTTPS.

### Without SSL (HTTP only)

Use `docker-compose.prod.yml` alone, the command at the top of this guide. FreeFrame then runs on **HTTP port 80** only, whatever `DOMAIN` and `ACME_EMAIL` are set to. This is fine for:
- Local testing of the production build
- Running behind an external reverse proxy that handles SSL

### Behind an External Reverse Proxy (Caddy, nginx, etc.)

If FreeFrame sits behind another proxy that already handles SSL:

1. Use `docker-compose.prod.yml` alone, without `docker-compose.https.yml`, so Traefik runs in HTTP mode
2. Point your external proxy to FreeFrame's port 80
3. Ensure the proxy forwards these headers: `X-Real-IP`, `X-Forwarded-For`, `X-Forwarded-Proto`
4. Set `FRONTEND_URL` in `.env.prod` to your `https://` URL

### Behind Cloudflare (proxied, orange cloud)

Cloudflare terminates the visitor's TLS and connects to the server on port 443. `docker-compose.cloudflare.yml` makes Traefik present a **Cloudflare Origin CA certificate** there, so the zone can use SSL mode **Full (strict)**. Use it *instead of* `docker-compose.https.yml`: Let's Encrypt's HTTP challenge does not get through the proxy.

1. **Certificate.** Create a private key and CSR on the server, outside the repository:
   ```bash
   install -d -m 700 /opt/freeframe-certs && cd /opt/freeframe-certs
   openssl req -new -newkey rsa:2048 -nodes -keyout origin.key -subj "/CN=review.example.com" -out origin.csr
   chmod 600 origin.key
   ```
   In Cloudflare go to **SSL/TLS > Origin Server > Create Certificate**, choose *Use my private key and CSR*, paste `origin.csr`, and list your FreeFrame hostname. Save the certificate as `/opt/freeframe-certs/origin.pem`. The private key never leaves the server.
2. **`.env.prod`:**
   ```bash
   FRONTEND_URL=https://review.example.com
   CLOUDFLARE_ORIGIN_CERT_DIR=/opt/freeframe-certs
   CLIENT_IP_HEADER=cf-connecting-ip
   ```
   Behind the proxy every request reaches Traefik from a Cloudflare edge address, so without `CLIENT_IP_HEADER` the per-IP rate limits (sign-in, share links) would count many visitors as one.
3. **Start:**
   ```bash
   docker compose --env-file .env.prod -f docker-compose.prod.yml -f docker-compose.cloudflare.yml up -d
   ```
4. **Close the origin to everyone but Cloudflare.** Otherwise anyone can skip Cloudflare by using the server's IP, and send their own `CF-Connecting-IP`. Docker publishes ports past `ufw`, so the rules go in Docker's `DOCKER-USER` chain:
   ```bash
   install -m 755 deploy/cloudflare-firewall.sh /usr/local/sbin/freeframe-cloudflare-firewall
   install -m 644 deploy/systemd/freeframe-cloudflare-firewall.service /etc/systemd/system/
   systemctl daemon-reload && systemctl enable --now freeframe-cloudflare-firewall
   ```
   It reapplies at boot and whenever Docker restarts, refreshing Cloudflare's published ranges each time (and keeping the last list if that fetch fails). Only ports 80 and 443 are affected.
5. **Cloudflare:** set SSL/TLS mode to **Full (strict)**. To leave other hostnames in the zone alone, do it with a Configuration Rule for this hostname only.

Uploads and playback go straight between the browser and your S3 storage, so Cloudflare's 100 MB request-body limit does not apply to them.

### Sub-path Deployments

To serve FreeFrame under a path instead of the domain root — `https://example.com/freeframe/` — set the base path and rebuild the web image:

```
NEXT_PUBLIC_BASE_PATH=/freeframe
FRONTEND_URL=https://example.com/freeframe
```

`NEXT_PUBLIC_BASE_PATH` is a **build-time** variable for the Docker image (Compose passes it as a build arg), so changing it needs `docker compose --env-file .env.prod -f docker-compose.prod.yml up -d --build web`, not a restart. (`next dev` in the dev stack reads it when the server starts.) Everything the web app serves moves under the prefix: pages, links, static assets, icons and raw navigations. The API stays at the origin root (`/api`), and links built from `FRONTEND_URL` carry the path, so invite and share emails keep pointing inside the mounted app. CORS matches the bare origin, since a browser's `Origin` header never carries a path.

If something sits in front of FreeFrame (a landing page, another app), forward the sub-path to FreeFrame **unchanged** — Next expects the prefix — and forward the API at the origin root as well, because that is where the browser still calls it, including the request bodies it sends. With nginx that is three `proxy_pass` locations, none with a URI part:

```nginx
# Two locations for the sub-path, not one. With trailingSlash unset, Next
# answers /freeframe/ with a 308 to /freeframe, and a lone "location /freeframe/"
# answers /freeframe with nginx's own 301 back, so the two would bounce forever.
# The exact match takes /freeframe itself before that 301 can happen. The
# slash-terminated prefix takes everything under it without also swallowing
# siblings such as /freeframe-docs or /freeframe.html, which a bare
# "location /freeframe" would send to FreeFrame's 404.
location = /freeframe {
    proxy_pass http://127.0.0.1:80;
}

location /freeframe/ {
    proxy_pass http://127.0.0.1:80;
}

location /api/ {
    # Project posters are the only large bodies the browser sends to the API.
    # The API accepts 10 MB; nginx's 1 MB default would reject them with an
    # HTML 413 the frontend cannot parse.
    client_max_body_size 10m;
    proxy_pass http://127.0.0.1:80;
}
```

Traefik in the bundled Compose already routes the sub-path to the web app and `/api` to the API, so forwarding both to FreeFrame's port 80 is enough; only a proxy that terminates the sub-path itself has to name the API separately. Leave `NEXT_PUBLIC_BASE_PATH` empty for a root deployment (the default).

---

## Bring Your Own Infrastructure

FreeFrame's Docker Compose includes PostgreSQL and Redis by default, but you can use external managed services instead.

### External Database (PostgreSQL)

Works with: **AWS RDS, Google Cloud SQL, Supabase, Neon, DigitalOcean Managed DB, or any PostgreSQL 15+ instance.**

1. Remove the `postgres` service and `pgdata` volume from `docker-compose.prod.yml`
2. Remove `postgres` from the `depends_on` of the `api`, `worker`, and `maintenance_worker` services
3. In `.env.prod`, set `DATABASE_URL` to your external database:
   ```
   DATABASE_URL=postgresql://user:password@your-db-host:5432/freeframe
   ```
4. Run migrations once manually on first deploy:
   ```bash
   docker compose --env-file .env.prod -f docker-compose.prod.yml run --rm api sh -c "cd /workspace/apps/api && alembic upgrade head"
   ```

### External Redis / Valkey

Works with: **AWS ElastiCache, Upstash, Redis Cloud, DigitalOcean Managed Redis, or any Redis 7+ / Valkey instance.** Valkey is a drop-in Redis replacement and works out of the box.

1. Remove the `redis` service and `redisdata` volume from `docker-compose.prod.yml`
2. Remove `redis` from the `depends_on` of the `api`, `worker`, `email_worker`, `maintenance_worker`, and `beat` services
3. In `.env.prod`, set `REDIS_URL` to your external instance:
   ```
   REDIS_URL=redis://:password@your-redis-host:6379/0
   ```

### External S3 Storage

Works with: **AWS S3, Cloudflare R2, Backblaze B2, DigitalOcean Spaces, Silo, Garage, MinIO, or any S3-compatible service.**

The production compose runs no S3 service by default — you provide your own. (`docker-compose.prod.yml` ships a commented-out block running [Silo](https://github.com/pgsty/silo), a maintained fork of MinIO, if you want to keep media on the same host; see the notes above it. The `minio/minio` and `minio/mc` images it used before can no longer be pulled from Docker Hub. Silo reads the same `MINIO_*` variables and opens an existing MinIO data volume as is.) Configure in `.env.prod`:

```
S3_STORAGE=s3
S3_BUCKET=your-bucket-name
S3_ACCESS_KEY=YOUR_ACCESS_KEY
S3_SECRET_KEY=YOUR_SECRET_KEY
S3_REGION=us-east-1
```

`S3_BUCKET` must be a valid S3 bucket name: 3-63 characters, lowercase letters, digits, hyphens and dots only. Underscores and capitals are rejected.

For **non-AWS S3-compatible providers** (R2, B2, Spaces, MinIO, Hetzner, …), set `S3_STORAGE` to a non-`s3` value (e.g. `minio`) so `S3_ENDPOINT` is used — with `S3_STORAGE=s3` the client talks to native AWS and the endpoint is **ignored** (FreeFrame refuses to start if `s3` is combined with a non-AWS endpoint). Then set the endpoint:

| Provider | S3_ENDPOINT |
|----------|-------------|
| Cloudflare R2 | `https://<account-id>.r2.cloudflarestorage.com` |
| Backblaze B2 | `https://s3.<region>.backblazeb2.com` |
| DigitalOcean Spaces | `https://<region>.digitaloceanspaces.com` |
| Silo or MinIO (self-hosted) | `http://your-silo-host:9000` |
| Garage (self-hosted) | `http://your-garage-host:3900` (or your reverse-proxy URL) |

In non-AWS mode FreeFrame always uses **path-style addressing** (`endpoint/bucket/key`) with **SigV4** signatures — the compatibility baseline every S3-compatible provider accepts. No wildcard bucket DNS is needed in front of a self-hosted store.

#### Bucket CORS — required for uploads

Uploads go **directly from the browser to your bucket** via presigned URLs, so the bucket's CORS must allow your FreeFrame origin.

`ExposeHeaders: ["ETag"]` is still recommended and is included below, but it is no longer required on any backend that supports `ListParts`, which is all of the ones above: the server reads an upload's parts from storage when completing it rather than relying on the browser having read each part's `ETag` response header. It is only used as a fallback on a backend that cannot list parts at all, and that case is logged as a warning. If you are debugging an upload that fails partway, `ETag` exposure is no longer the first thing to suspect.

```json
{
  "CORSRules": [{
    "AllowedOrigins": ["https://your-freeframe-domain.example"],
    "AllowedMethods": ["GET", "PUT", "POST", "HEAD"],
    "AllowedHeaders": ["Content-Type", "Content-MD5", "x-amz-content-sha256", "x-amz-date", "x-amz-decoded-content-length"],
    "ExposeHeaders": ["ETag"],
    "MaxAgeSeconds": 3000
  }]
}
```

`DELETE` is intentionally absent — FreeFrame has no presigned-DELETE flow, and the browser never needs it against the bucket. The `AllowedHeaders` list above is exactly what browser uploads and HLS segment fetches send; `["*"]` also works on most backends, but the list is sufficient and is what FreeFrame's automatic startup config now applies.

If you allow **more than one origin**, give each origin its own rule rather than listing them all in one. Some backends (Garage) answer a multi-origin rule by joining every entry into a single comma-separated `Access-Control-Allow-Origin` header, which browsers reject — every upload and HLS segment fetch then fails CORS. FreeFrame's automatic startup config already emits one rule per origin.

FreeFrame applies this automatically to **non-AWS** buckets at startup when it has permission (and logs a warning if it can't). The rules allow `FRONTEND_URL`, `http://localhost:3000` and every origin in `CORS_ALLOW_ORIGINS`, the same origins the API accepts; `CORS_ALLOW_ORIGINS=*` becomes a single rule allowing any origin. Older MinIO releases answered this call with `NotImplemented` and allowed every origin regardless, so a browser origin missing from that list only starts failing once you move to a store that enforces bucket CORS (Silo, Garage): add it to `CORS_ALLOW_ORIGINS`. Set it yourself for **AWS S3**, or wherever FreeFrame lacks CORS permission. **Hetzner Object Storage** exposes CORS only via API/CLI (not the Console UI), so it's easy to miss — apply the JSON above with `aws s3api put-bucket-cors`.

#### Abort incomplete multipart uploads — recommended

Add a bucket lifecycle rule that **aborts incomplete multipart uploads after a few days**. It is a backstop: it cleans up the rare empty multipart upload left behind when a request to start an upload times out after storage had already created it. It also frees the parts of any upload that is never completed, which S3 bills for until the upload is aborted. On AWS the API's reaper also finds those empty uploads itself, since nothing in the database refers to them; the rule covers the stores where it cannot.

The rule counts days from when the upload **started**, not from when it last moved. FreeFrame keeps offering Resume on an upload for as long as it keeps moving: its own reaper (`STALE_UPLOAD_TIMEOUT_HOURS`, 24 hours by default) ages an upload by its last activity. So the rule can abort an upload that someone is still resuming, and that upload is lost: its next part or its completion fails, and the file has to be sent again from the start. Pick a number of days above the longest upload you expect people to keep resuming, and above `STALE_UPLOAD_TIMEOUT_HOURS`. For scale, 600 GB at 8 Mbit/s takes about a week, and an upload resumed a little each day can run longer. The 7 days below suits most instances; raise it if yours takes very large files over slow links.

On **AWS S3** (and any store that implements the S3 lifecycle API), save this as `lifecycle.json`:

```json
{
  "Rules": [{
    "ID": "abort-incomplete-multipart-uploads",
    "Status": "Enabled",
    "Filter": { "Prefix": "" },
    "AbortIncompleteMultipartUpload": { "DaysAfterInitiation": 7 }
  }]
}
```

and apply it:

```bash
aws s3api put-bucket-lifecycle-configuration --bucket YOUR_BUCKET --lifecycle-configuration file://lifecycle.json
```

That call **replaces** the bucket's whole lifecycle configuration, so if the bucket already has rules, read them first with `aws s3api get-bucket-lifecycle-configuration --bucket YOUR_BUCKET` and add this rule to them. For a store that is not AWS, add `--endpoint-url` with its S3 endpoint. The same rule can be created in the AWS console, under the bucket's **Management** tab (lifecycle rules).

**MinIO and Silo** have no equivalent rule to set: `mc ilm rule add` has no option for it, and Silo's `mc ilm import` rejected a rule with `AbortIncompleteMultipartUpload` as not valid for its lifecycle schema. MinIO expires abandoned multipart uploads on its own, after about 24 hours, so there is nothing to configure there. On either, the API's own reaper (`STALE_UPLOAD_TIMEOUT_HOURS`) still reclaims the uploads it has a record of.

#### Streaming projects — originals deleted after a retention period

A project owner can turn on **Streaming project** under **Project settings > General**. It is meant for projects whose clips are shared and watched rather than reviewed and handed back as masters. In a streaming project:

- Every video keeps a **web MP4** (`download.mp4`, the top rendition remuxed into one file) next to its HLS ladder, even with `TRANSCODER_DOWNLOAD_MP4=false`. Download serves that MP4. Versions transcoded without one get it built from their ladder (a `build_download_mp4` job on the transcoding queue) before anything is deleted.
- The **uploaded original of each video version is deleted `STREAMING_ORIGINAL_RETENTION_DAYS` after the version became ready** (default 30; `0` switches the deletion off). The `delete_streaming_originals` task runs daily at 03:30 UTC on the maintenance queue. Images and audio are never touched.
- **Deleted originals are gone for good.** Nothing can bring them back, turning streaming mode off only stops future deletions, and download menus stop offering "Original" for those versions. Do not use streaming mode for anything you may need the master of.
- Review projects (streaming mode off, the default) are unaffected.

**Bucket locks.** If the bucket has an object lock or retention rule on `raw/` (for example a Cloudflare R2 bucket lock rule), set `STREAMING_ORIGINAL_RETENTION_DAYS` to at least the lock period. Storage refuses to delete a locked original (R2 answers `409 ObjectLockedByBucketPolicy`); the task logs a warning, leaves the original recorded as present and tries again on the next run, so a shorter retention only means the originals go once the lock expires.

Streaming projects are also what makes a share link **play inline in Discord** (and other OpenGraph link previews): the share page advertises the version's web MP4 as `og:video`, and only a version that has one gets a playable embed. In a review project with `TRANSCODER_DOWNLOAD_MP4=false` the preview is the thumbnail only. While a video has no ready version yet, its newest processing version is embedded from the uploaded original instead, when a browser can play it (MP4, WebM, QuickTime) and it has not been deleted, in any kind of project; the thumbnail and size it shows are recorded when the transcode starts. This matters because Discord keeps the preview from the first time it fetches a link, so a link posted during processing would otherwise never get a player. An original Discord cannot decode (ProRes, often HEVC) will not play there. Link previews are shown for public, passwordless, enabled links to a single asset; password-protected and sign-in-required links show none. If the site is behind Cloudflare, make sure no WAF/bot rule blocks the `Discordbot` user agent on the web app and on `/share/*/embed.*` of the API, or Discord shows a bare link.

The share page writes the video as `og:video:url` with its size, beside `og:type=video.other` and the thumbnail as `og:image`, and deliberately leaves out `og:video:type`, `og:video:secure_url` and the `twitter:player` tags: with those present Discord sizes the file and shows no player for anything over its embed limit (a 272 MB MP4 got none), while this minimal set plays inline whatever the size. Discord only shows iframe players (`twitter:player` pages) for a fixed list of partner sites such as YouTube, so a direct video file is the only inline option.

### External SMTP

> **⚠️ Email is needed for sign-in codes and invites.** Without a mailer, invites and notifications are not delivered, and sign-in codes are switched off: with `EMAIL_CODE_LOGIN=auto` (the default) the login page offers only password sign-in (and [Sign in with Discord](#sign-in-with-discord), if configured) until `SMTP_HOST` is set or `MAIL_PROVIDER=ses`. Set `EMAIL_CODE_LOGIN=true` or `false` to force it either way. The app logs a warning at startup when email isn't configured.

Works with: **Mailgun, Postmark, SendGrid, Amazon SES, or any SMTP server.**

Configure in `.env.prod`:

**SMTP (most providers):**
```
MAIL_PROVIDER=smtp
MAIL_FROM_ADDRESS=noreply@your-domain.com
# Blank follows the instance branding name; set it to pin a fixed one.
MAIL_FROM_NAME=
SMTP_HOST=smtp.mailgun.org
SMTP_PORT=587
SMTP_USER=your-smtp-user
SMTP_PASSWORD=your-smtp-password
SMTP_USE_TLS=true
```

**AWS SES:**
```
MAIL_PROVIDER=ses
MAIL_FROM_ADDRESS=noreply@your-domain.com
# Blank follows the instance branding name; set it to pin a fixed one.
MAIL_FROM_NAME=
AWS_MAIL_ACCESS_KEY_ID=YOUR_KEY
AWS_MAIL_SECRET_ACCESS_KEY=YOUR_SECRET
AWS_MAIL_REGION=us-east-1
```

---

## Sign in with Discord

FreeFrame can offer **Continue with Discord** on the login page. It is off until you configure it.

1. In the [Discord Developer Portal](https://discord.com/developers/applications), create an application (or open an existing one) and go to **OAuth2**.
2. Copy the **Client ID**, and reset and copy the **Client Secret**.
3. Under **Redirects**, add the redirect URI shown in FreeFrame's **Sign in with Discord** card (**Settings > Admin > Instance settings**). For a standard install it is `https://<your-domain>/api/auth/discord/callback` (the origin of `FRONTEND_URL`, even on a sub-path install, because the API stays at `/api`). Set `DISCORD_OAUTH_REDIRECT_URI` only if the API is reached somewhere else; in the dev stack, where the browser calls the API directly, that is `http://localhost:8000/auth/discord/callback`.
4. In that card, enter the Client ID and Client secret and turn it on. You can set the same values with `DISCORD_LOGIN_ENABLED`, `DISCORD_CLIENT_ID` and `DISCORD_CLIENT_SECRET` instead; a value saved in the app wins over its variable, and clearing it there falls back to the variable.

Who can sign in depends on whether a **Server ID** is set:

- **No server ID:** Discord only signs in people who already have a FreeFrame account, or a pending invite, under the email address verified on their Discord account. Signing in this way accepts a pending invite. Nobody else gets an account; they see "No FreeFrame account uses this Discord email."
- **Server ID set** (`DISCORD_SERVER_ID`, the ID of your Discord server; turn on Developer Mode in Discord, then right-click the server and Copy Server ID): only members of that server can sign in, checked on every sign-in, so someone who leaves the server can no longer use Discord to get in. **A member without a FreeFrame account gets one on their first sign-in** (a regular member, never an admin), named after their server nickname or Discord display name. Add **Allowed role IDs** (`DISCORD_SERVER_ROLES`, comma-separated) to require at least one of those roles as well.

The Discord account's email must be verified on Discord. A deactivated FreeFrame account cannot sign in with Discord.

The client secret is stored in the database encrypted, with a key derived from `JWT_SECRET`, and the admin settings never show it again. **Changing `JWT_SECRET` makes the stored secret unreadable**: Discord sign-in then reports itself as not configured until you enter the secret again (a secret set with `DISCORD_CLIENT_SECRET` is not affected).

### Project invite links

Without email, invites can't reach anyone, so a project owner can invite people with a link instead: **Project settings > People > Invite link** creates a single-use link (`<FRONTEND_URL>/join/<token>`) for a chosen role (Viewer, Reviewer or Editor) that expires after 1, 7 or 30 days, or never. The URL is shown once; only a hash of it is stored. Each link admits **one** person: the first to use it joins the project and the link is spent (the People tab then shows who used it and when), so make a new link for each person. Revoke an unused link from the same list.

- **Signed out**, the link offers **Continue with Discord**. A valid link lets its holder past the server and role check above, even with a server ID set, and someone with no FreeFrame account gets one (a regular member, never an admin). They are added to that one project and see no other private project.
- **Signed in**, the link offers **Join project**. This works without Discord too: with Discord sign-in off, the page sends people to password sign-in and back.
- Someone already in the project with the link's role or a higher one keeps their role and does not use up the link.
- **Exempt accounts:** an account created through a link by someone who does *not* pass the server check (not in the server, or without an allowed role) is marked `discord_gate_exempt`, so it keeps signing in with Discord without being in the server; it reaches only the projects it has been added to. Someone who passes the check when they use the link is not exempt, so leaving the server or losing the role still locks them out, as for any account created by server membership. An existing account that uses a link gets the membership, not the exemption.
- A link checked when sign-in starts is checked again when Discord sends the person back, so a link used, revoked or expired in between is refused with "This invite link is invalid or has expired."

---

## Configuration Reference

All environment variables are documented in [`.env.example`](../.env.example). Key settings:

| Variable | Description | Default |
|----------|-------------|---------|
| `DATABASE_URL` | PostgreSQL connection string | (required) |
| `REDIS_URL` | Redis connection string | (required) |
| `S3_STORAGE` | `s3` for native AWS S3; any other value (e.g. `minio`) for every other S3-compatible store | `minio` |
| `S3_BUCKET` | S3 bucket name | (required) |
| `S3_ENDPOINT` | S3 endpoint the API and workers use (non-AWS); ignored when `S3_STORAGE=s3` | `http://minio:9000` |
| `S3_PUBLIC_ENDPOINT` | Browser-reachable base URL for presigned URLs, when it differs from `S3_ENDPOINT` | (empty = `S3_ENDPOINT`) |
| `S3_STORAGE_CLASS` | Storage class for media (uploaded originals, every transcoder output and watermarked renders), set by the server when each object is created. Avatars, logos and posters keep the bucket default. MinIO and Silo accept only `STANDARD` and `REDUCED_REDUNDANCY`; Garage ignores it; `GLACIER` and `DEEP_ARCHIVE` are refused at startup | (empty = bucket default) |
| `CORS_ALLOW_ORIGINS` | Extra browser origins, comma-separated, allowed by the API and (non-AWS) the bucket | (empty) |
| `JWT_SECRET` | Auth token signing key | (required, generate with `openssl rand -hex 64`) |
| `FRONTEND_URL` | Your FreeFrame URL (with https://) | (required) |
| `DOMAIN` | Your domain; required with `docker-compose.https.yml`, which requests the certificate | (optional) |
| `ACME_EMAIL` | Email for Let's Encrypt notifications; required with `docker-compose.https.yml` | (optional) |
| `DISCORD_LOGIN_ENABLED`, `DISCORD_CLIENT_ID`, `DISCORD_CLIENT_SECRET` | Sign in with Discord, as a fallback for the values saved under Settings > Admin > Instance settings (see [Sign in with Discord](#sign-in-with-discord)) | (empty = off) |
| `DISCORD_SERVER_ID`, `DISCORD_SERVER_ROLES` | Restrict Discord sign-in to members of one server (with one of these role IDs); members without an account get one on first sign-in | (empty) |
| `DISCORD_OAUTH_REDIRECT_URI` | Redirect URI registered on the Discord application | `<FRONTEND_URL origin>/api/auth/discord/callback` |
| `MAIL_PROVIDER` | `smtp` or `ses` | `smtp` |
| `API_WORKERS` | Gunicorn worker processes | `4` |
| `TRANSCODING_CONCURRENCY` | Parallel transcoding jobs | `2` |
| `EMAIL_CONCURRENCY` | Parallel email jobs | `2` |
| `MAINTENANCE_CONCURRENCY` | Parallel housekeeping jobs | `1` |
| `CELERY_LOGLEVEL` | Log level of the Celery workers and beat. At `warning` they still log what the retention GC, stale-upload reaper and orphan sweep deleted, and any sweep that is switched off | `info` |
| `NEXT_PUBLIC_UPLOAD_CONCURRENCY` | Upload parts in flight across the whole browser tab, shared by every file being uploaded (the earliest file first), not per file. Values above 6 only help when the storage endpoint speaks HTTP/2: over HTTP/1.1 a browser opens at most 6 connections per host, shared by all its tabs, so parts past the sixth wait for a free connection before they send anything, which with large parts on a slow uplink can be minutes. The upload leaves such a waiting part alone while other parts in the tab are sending, and gives up on it (and retries it) only after 5 minutes without a first byte. Build-time: changing it needs a web image rebuild | `5` |
| `TRANSCODER_PIPELINE` | GPU backend: `Auto`, `NVIDIA`, `Intel`, or `Software` | `Auto` |
| `TRANSCODER_OUTPUT` | Output codec: `h264_8` (broad compatibility) or `h265_10` (HEVC 10-bit) | `h264_8` |
| `TRANSCODER_HDR` | `convert` (tone-map HDR to SDR) or `preserve` (keep HDR tags) | `convert` |
| `TRANSCODER_QUALITIES` | Ladder rungs to build: any of `2160p`, `1080p`, `720p`, `360p`. A rung names the source's short side, so portrait video is sized like landscape (1080x1920 under `1080p` keeps its size). Rungs above the source are dropped, and if that leaves none the smallest is built at the source's own size, so `2160p` alone gives one rendition at source size for anything up to 4K | `1080p,720p,360p` |
| `TRANSCODER_AUDIO_BITRATE` | AAC bitrate for the audio of video review streams, as `<n>k` between `64k` and `512k` (for example `320k` for music and final-mix review). Empty leaves ffmpeg's own choice, AAC-LC at about 128 kb/s for stereo. The value is the total for all channels, and ffmpeg's default for a surround source is already higher (about 341 kb/s for 5.1 and 469 kb/s for 7.1), so a stereo-sized value such as `320k` lowers surround quality. The codec is always AAC-LC, the one every browser plays over HLS. Applies to every rendition, so storage grows by the extra bitrate times the number of rungs. A source-copied upload whose audio is already AAC keeps its own audio untouched; non-AAC audio in a copy is encoded at this bitrate, or `192k` when it is empty. An unusable value is logged and ignored | *(empty)* |
| `TRANSCODER_SOURCE_COPY` | Package an already browser-safe source into HLS without re-encoding, where the ladder above resolves to one rendition at the source's own size | `false` |
| `TRANSCODER_CPU_LIMIT` | Cap the CPU one transcode takes: a core count (`6`) or a share of what the process may use (`50%`). Empty = unbounded. Covers the encoders and the filter graph, not decoding, so a heavy master can run over by roughly a quarter, and a low budget can push a long master past ffmpeg's 4-hour ceiling — see `.env.example`. Per ffmpeg process, so the machine-wide ceiling is this times `TRANSCODING_CONCURRENCY` | *(empty)* |

Upload part size is set in the app rather than the environment: **Admin Dashboard → Instance settings → Uploads**. Automatic (the default) keeps 10 MB parts up to ~5 GB and grows them for bigger files to about 500 parts, which keeps the storage backend's assembly at the end of an upload short. Fixed cuts every upload into one size from 5 to 512 MB, and refuses a file that would need more than 10,000 parts. A completion that takes 5 seconds or more is logged by the API with where the time went (`completing upload … took …`), which is the place to look when uploads wait at "Finalizing".

Uploads wait in a queue. Files dropped or picked together start in the order they were added, with up to three files active at a time, and the earliest file gets the bandwidth first, so a lost connection leaves whole finished files rather than many half-sent ones. `NEXT_PUBLIC_UPLOAD_CONCURRENCY` is the one budget of upload parts those files share: it used to apply to each file on its own, so with several files uploading at once a tab now sends no more parts than the setting says, where it used to send that many per file. If you raised it to speed up a single large file, it still does. If storage is unreachable when a file's turn comes, the queue pauses (the Uploads panel says "Waiting for storage" or "Waiting for connection") and carries on by itself when it answers again.

Who may download originals is also set in the app: **Admin Dashboard → Instance settings → Downloads**. *Transcode only* (the default) gives share-link visitors, visitors to a public project and people an asset is shared with directly the transcode (a video's MP4, an audio file's MP3, an image's WebP), as earlier releases did; a video with no MP4 still downloads its original. *Original or transcode* lets them pick the original too. Project members can always pick the original one asset at a time. Bulk downloads (a selection on the project page, Download All on a share link) follow this setting for everyone: originals when it is on, transcodes when it is off. Camera masters can be many times the size of the transcode, so turning it on can raise storage egress considerably.

---

## Scaling

### API Workers

The `API_WORKERS` env var controls how many gunicorn worker processes handle API requests. A good starting point:

```
API_WORKERS = (2 x CPU cores) + 1
```

### Transcoding Workers

Video transcoding is CPU-intensive. Adjust `TRANSCODING_CONCURRENCY` based on your server:

| Server | Recommended |
|--------|-------------|
| 2 cores | 1-2 |
| 4 cores | 2-3 |
| 8+ cores | 4-6 |

### Hardware-Accelerated Transcoding (optional)

FreeFrame can use an NVIDIA (NVENC) or Intel (VAAPI) GPU to transcode faster, and
automatically falls back to software (CPU) transcoding if no GPU is present or a
hardware attempt fails at runtime. This is entirely opt-in — the default build and
default runtime behavior are unchanged.

To enable it:
1. Build the API image with `--build-arg ENABLE_HWACCEL=true` (Intel/VAAPI only —
   NVENC needs no extra packages in the image, just the host driver).
2. Give the `worker` service access to the GPU: pass through `/dev/dri` for VAAPI,
   or install `nvidia-container-toolkit` and add a GPU reservation for NVENC.
3. Set `TRANSCODER_PIPELINE` to `NVIDIA`, `Intel`, or leave it as `Auto` to detect
   automatically.

> Compose-file examples for GPU device passthrough aren't included yet — that's a
> known follow-up.

If you build the `2160p` rung, a GPU is worth it: a 4K encode takes about four times
the CPU of a 1080p one. A master that is already H.264 8-bit 4:2:0 can skip the
encode entirely with `TRANSCODER_SOURCE_COPY=true` and a single-rung ladder, but most
4K camera and NLE exports (HEVC, 10-bit, HDR, ProRes) cannot, and are always encoded.
The GPU decoders don't handle ProRes, DNxHR, 10-bit H.264, or 4:2:2 H.264 or HEVC.
For those masters, on NVIDIA and Intel, the decode stays on the CPU and the frames
are uploaded, so scaling and encoding still run on the GPU. Expect the CPU decode
to be the bottleneck on 4K ProRes. Video with a display rotation (most portrait
phone footage) has to be turned upright in system memory: on NVIDIA it is still
decoded on the GPU and passed through system memory, which costs a little CPU; on
Intel it is decoded on the CPU.

### Email Workers

Email sending is I/O-bound and lightweight. The default of `2` is sufficient for most deployments.

### Maintenance Workers

Housekeeping is I/O-bound but can run long: `cleanup_soft_deleted` deletes S3
objects one at a time and `sweep_orphan_s3` lists the whole bucket. The default
of `1` is right for most deployments, since these tasks are scheduled hourly at
most and none of them is latency-sensitive. Raising it mainly helps if you have
a large backlog of soft-deleted media to reclaim.

---

## Monitoring

### Health Check

The API exposes a health endpoint:

```
GET /health → { "status": "ok" }
```

Use this for uptime monitoring (UptimeRobot, Healthchecks.io, etc.) or Docker health checks.

### Logs

```bash
# Follow all service logs
docker compose --env-file .env.prod -f docker-compose.prod.yml logs -f

# Follow a specific service
docker compose --env-file .env.prod -f docker-compose.prod.yml logs -f api

# Last 100 lines
docker compose --env-file .env.prod -f docker-compose.prod.yml logs --tail 100 api
```

### Key Metrics to Watch

| Metric | How to Check | Warning Sign |
|--------|-------------|--------------|
| Disk space | `df -h` | > 80% used |
| Memory | `free -m` | Swap in use |
| API response | `curl -s localhost/health` | Non-200 response |
| Queue depth | `docker compose --env-file .env.prod -f docker-compose.prod.yml exec redis sh -c 'redis-cli -a "$REDIS_PASSWORD" --no-auth-warning LLEN transcoding'` (repeat for `email_high`, `email_low`, `maintenance`, `default`) | Any queue growing steadily; **any depth at all on `default`** |
| Busy workers | `docker compose --env-file .env.prod -f docker-compose.prod.yml exec api celery -A apps.api.tasks.celery_app inspect active` | Tasks stuck for hours |
| Database connections | `docker compose --env-file .env.prod -f docker-compose.prod.yml exec postgres psql -U freeframe -c "SELECT count(*) FROM pg_stat_activity;"` | > 80% of max |

Check depth with `LLEN`, not `inspect active`. `inspect active` reports what
connected workers are executing right now, so a queue that no worker subscribes
to shows up as nothing at all: the workers are genuinely idle while the backlog
grows unseen. That is exactly how [#240](https://github.com/Techiebutler/freeframe/issues/240)
went unnoticed.

`REDIS_PASSWORD` lives in `.env.prod`, which is not exported into your shell,
so it has to expand inside the container. That is why the command above is
wrapped in `sh -c '...'` with single quotes rather than passing `-a "$REDIS_PASSWORD"`
directly, which would authenticate with an empty password.

`default` should always be empty. Every task is routed explicitly, so anything
landing there is a task that was added without a route and that no worker will
ever run.

### Queue Topology

Each worker subscribes to an explicit `-Q` list, so a task whose queue has no
subscriber is silently never executed. The shipped topology is:

| Queue | Consumed by | Carries |
|-------|-------------|---------|
| `transcoding` | `worker` | Video/audio/image processing, metadata backfill, watermarking |
| `email_high` | `email_worker` | Magic codes, invites |
| `email_low` | `email_worker` | Mentions, comments, approvals, share notices |
| `maintenance` | `maintenance_worker` | Retention GC, stale-upload reaper, orphan sweep |
| `default` | nobody, by design | Should always be empty |

If you add a task, route it in `apps/api/tasks/celery_app.py` to one of the
consumed queues. `apps/api/tests/test_celery_queue_topology.py` fails if a
registered task routes somewhere no compose worker consumes.

---

## Backups

### Database Backup

```bash
# One-time backup
docker compose --env-file .env.prod -f docker-compose.prod.yml exec postgres \
  pg_dump -U freeframe freeframe | gzip > backup_$(date +%Y%m%d_%H%M%S).sql.gz

# Restore from backup
gunzip -c backup_20260403_120000.sql.gz | \
  docker compose --env-file .env.prod -f docker-compose.prod.yml exec -T postgres \
  psql -U freeframe freeframe
```

### Automated Daily Backups

Add a cron job on your server:

```bash
# Edit crontab
crontab -e

# Add this line (runs daily at 2 AM, keeps 30 days)
0 2 * * * cd /path/to/freeframe && docker compose --env-file .env.prod -f docker-compose.prod.yml exec -T postgres pg_dump -U freeframe freeframe | gzip > /path/to/backups/freeframe_$(date +\%Y\%m\%d).sql.gz && find /path/to/backups -name "freeframe_*.sql.gz" -mtime +30 -delete
```

### S3 Media Backup

Your media files are already in S3. For redundancy:

- **AWS S3**: Enable [versioning](https://docs.aws.amazon.com/AmazonS3/latest/userguide/Versioning.html) and [cross-region replication](https://docs.aws.amazon.com/AmazonS3/latest/userguide/replication.html)
- **Cloudflare R2**: Use [Sippy](https://developers.cloudflare.com/r2/data-migration/sippy/) for incremental migration/backup
- **Self-hosted Silo or MinIO**: Use [`mc mirror`](https://min.io/docs/minio/linux/reference/minio-mc/mc-mirror.html) to replicate to a second location

### What to Back Up

| Data | Location | Priority |
|------|----------|----------|
| Database | PostgreSQL | **Critical** — all users, projects, comments, share links |
| Media files | S3 bucket | **Important** — uploaded assets and transcoded files |
| Environment config | `.env.prod` | **Important** — save a copy outside the server |
| SSL certificates | `letsencrypt/` volume | Low — Traefik auto-renews them |

---

## Updating

```bash
cd freeframe
git pull origin main
docker compose --env-file .env.prod -f docker-compose.prod.yml up -d --build
```

Database migrations run automatically on API startup. Always check the [CHANGELOG](../CHANGELOG.md) before updating.

### Automatic deploys (GitHub Actions)

A fork can deploy itself on every push. In this repository the `Deploy` workflow (`.github/workflows/deploy.yml`) runs after `CI` completes on the `re-main` branch, and only when CI succeeded on a push (not a pull request). It connects to the server over SSH and runs the deploy script; a failed build or an API/web container that does not come up healthy fails the run. Afterwards it fetches `$DEPLOY_URL/api/health` and `$DEPLOY_URL/login`.

The deploy script, [`deploy/vps-deploy.sh`](../deploy/vps-deploy.sh), takes a lock so deploys run one at a time, resets the checkout in `/opt/freeframe` to `origin/re-main`, builds the images before restarting anything, runs `up -d --remove-orphans`, waits for the `api` and `web` health checks, and prunes old images. It logs to `/var/log/freeframe-deploy.log`. `APP_DIR`, `DEPLOY_BRANCH`, `COMPOSE_FILES` and `HEALTH_URL` override its defaults.

Set it up once:

1. Install the script on the server:
   ```bash
   install -m 755 deploy/vps-deploy.sh /usr/local/sbin/freeframe-deploy
   ```
2. Create a key pair for CI (`ssh-keygen -t ed25519 -N '' -f deploy_key`) and add the public key to the deploying user's `~/.ssh/authorized_keys` as a **forced command**, so the key can start a deploy and nothing else, whatever the client asks to run:
   ```
   command="/usr/local/sbin/freeframe-deploy",no-port-forwarding,no-agent-forwarding,no-X11-forwarding,no-pty ssh-ed25519 AAAA... freeframe-ci-deploy
   ```
3. Add the repository secrets `DEPLOY_HOST` (the server's address), `DEPLOY_SSH_KEY` (the private key) and `DEPLOY_KNOWN_HOSTS` (the output of `ssh-keyscan <host>`, which pins the server's host key), and the repository variable `DEPLOY_URL` (the public base URL, e.g. `https://review.example.com`). Then delete the local copy of the private key.

The job runs in the `production` environment, so protection rules added to that environment (required reviewers, for example) apply to it. To redeploy without a new commit, run the workflow by hand: **Actions > Deploy > Run workflow**, or `gh workflow run deploy.yml`. It always deploys the current `origin/re-main`.

### Upgrading past the maintenance-queue fix ([#240](https://github.com/Techiebutler/freeframe/issues/240))

Before this fix, no worker consumed the `default` queue, so the retention GC,
the stale-upload reaper, the orphan sweeper, due-date reminders and
`apply_watermark` were published and never executed. This release adds a
`maintenance_worker` service, so **`docker compose up -d --build` alone is not
enough: you must be using the updated compose file** for the new service to
exist.

Two things to do once, in this order.

**1. Check what the first garbage-collection run will delete.** The GC has never
run on your instance, so its first pass reclaims everything already past the
retention window, all at once. Count it first, substituting your own
`SOFT_DELETE_RETENTION_DAYS` for the `30` below:

```bash
docker compose --env-file .env.prod -f docker-compose.prod.yml exec postgres \
  psql -U freeframe -c "
WITH cutoff AS (SELECT now() - interval '30 days' AS t)
SELECT 'projects'        AS root, count(*) FROM projects,        cutoff WHERE deleted_at < t
UNION ALL SELECT 'folders',        count(*) FROM folders,        cutoff WHERE deleted_at < t
UNION ALL SELECT 'assets',         count(*) FROM assets,         cutoff WHERE deleted_at < t
UNION ALL SELECT 'asset_versions', count(*) FROM asset_versions, cutoff WHERE deleted_at < t
UNION ALL SELECT 'comments',       count(*) FROM comments,       cutoff WHERE deleted_at < t
UNION ALL SELECT 'share_links',    count(*) FROM share_links,    cutoff WHERE deleted_at < t
UNION ALL SELECT 'live assets swept in with a deleted project/folder',
       count(*) FROM assets a, cutoff
       WHERE a.deleted_at IS NULL AND (
         a.project_id IN (SELECT id FROM projects WHERE deleted_at < cutoff.t)
         OR a.folder_id IN (SELECT id FROM folders WHERE deleted_at < cutoff.t));"
```

That last row is the one that surprises people. Deleting a project sets
`deleted_at` on the project row only, and does **not** cascade to its assets, but
the purge removes every asset belonging to a purged project or folder regardless
of the asset's own `deleted_at`. So a project you trashed months ago still counts
its live assets here even though they look untouched.

If any of these numbers is larger than you expect, raise
`SOFT_DELETE_RETENTION_DAYS` (or set it to `0` to keep the GC off) before
starting the new worker, and lower it once you have reviewed what would go. The
deletion is a hard delete with S3 reclaim and is not recoverable.

**2. Drop the stale `default` backlog.** Instances that ran `beat` accumulated
roughly 49 orphaned messages a day. The new worker consumes `maintenance`, not
`default`, so the backlog is inert and safe to leave, but it wastes Redis
memory:

```bash
docker compose exec redis redis-cli -a "$REDIS_PASSWORD" LLEN default
docker compose exec redis redis-cli -a "$REDIS_PASSWORD" RENAME default default-old-240
```

`RENAME` rather than `DEL` so the messages can be inspected or restored. Delete
`default-old-240` once the new worker is running and `LLEN maintenance` is
draining normally.

### Upgrading past the media-metadata fix ([#124](https://github.com/Techiebutler/freeframe/issues/124))

If you're upgrading past the media-metadata fix ([#124](https://github.com/Techiebutler/freeframe/issues/124)), backfill missing `duration_seconds`/`width`/`height`/`fps` on already-processed files with: `docker exec freeframe-api-1 python -m apps.api.scripts.backfill_media_metadata`. The backfill runs as a Celery task on the `transcoding` queue, so it occupies one worker slot and can run long on large libraries (up to ~300s per file); new uploads keep transcoding normally on the remaining slots.

### Upgrading past the storage image switch ([#461](https://github.com/Techiebutler/freeframe/issues/461))

Only for deployments that run the optional self-hosted storage block in `docker-compose.prod.yml`. The `minio/minio` and `minio/mc` images can no longer be pulled from Docker Hub, so the block now runs Silo, a maintained fork of MinIO. A cached `minio/minio` image keeps working if you don't switch, but it can't be pulled again.

1. In your compose file, replace `minio/minio:...` with `pgsty/silo:RELEASE.2026-09-16T00-00-00Z` and `minio/mc:latest` with `pgsty/mc:RELEASE.2026-09-16T00-00-00Z`, and delete the four `MINIO_CORS_*` lines (MinIO never read them).
2. Keep the service name `minio` and the `miniodata` volume. Silo opens the existing data as is, and the old image can still read it if you roll back.
3. Silo enforces the bucket CORS rules FreeFrame writes at startup, which MinIO ignored. Make sure `FRONTEND_URL` is the exact origin people browse to, and put any other browser origin in `CORS_ALLOW_ORIGINS`. If your `.env.prod` sets `MINIO_CORS_ALLOW_ORIGIN`, move that value to `CORS_ALLOW_ORIGINS`.
4. `docker compose --env-file .env.prod -f docker-compose.prod.yml up -d`

### Update Checklist

1. **Read the changelog** — check for breaking changes or new env vars
2. **Backup the database** — `pg_dump` before updating (see [Backups](#backups))
3. **Pull and rebuild** — `git pull && docker compose up -d --build`
4. **Verify** — check `/health`, test login, spot-check a share link
5. **Rollback if needed** — `git checkout v1.x.x && docker compose up -d --build`

---

## Troubleshooting

### Services not starting

```bash
# Check logs for a specific service
docker compose --env-file .env.prod -f docker-compose.prod.yml logs api
docker compose --env-file .env.prod -f docker-compose.prod.yml logs worker
docker compose --env-file .env.prod -f docker-compose.prod.yml logs web
docker compose --env-file .env.prod -f docker-compose.prod.yml logs traefik

# Check all service statuses
docker compose --env-file .env.prod -f docker-compose.prod.yml ps
```

### Database migration failures

```bash
# Run migrations manually
docker compose --env-file .env.prod -f docker-compose.prod.yml run --rm api sh -c "cd /workspace/apps/api && alembic upgrade head"

# Check migration status
docker compose --env-file .env.prod -f docker-compose.prod.yml run --rm api sh -c "cd /workspace/apps/api && alembic current"
```

### SSL certificate not working

- Verify you started with `-f docker-compose.prod.yml -f docker-compose.https.yml`; the base file alone never requests a certificate
- Verify `DOMAIN` and `ACME_EMAIL` are set in `.env.prod`
- Check that DNS A record points to your server: `dig your-domain.com`
- Ensure ports 80 and 443 are open: `sudo ufw allow 80,443/tcp`
- Check Traefik logs: `docker compose --env-file .env.prod -f docker-compose.prod.yml -f docker-compose.https.yml logs traefik`
- Let's Encrypt has rate limits — if you hit them, wait an hour and retry

### S3 connection issues

- Verify your credentials are correct in `.env.prod`
- Ensure your bucket exists and has proper CORS configuration
- For non-AWS providers, double-check the `S3_ENDPOINT` URL
- **Uploads stuck at 0%, with a browser `NetworkError` / `Failed to fetch` on the part `PUT`:** on a self-hosted or S3-compatible store the bucket's CORS allows only `FRONTEND_URL`'s origin, `http://localhost:3000`/`3001` and `CORS_ALLOW_ORIGINS`. Check that `FRONTEND_URL` matches the address bar exactly (scheme, host and port), then recreate the API (`docker compose --env-file .env.prod -f docker-compose.prod.yml up -d --force-recreate api`) so it rewrites the rules. Then check that `S3_PUBLIC_ENDPOINT` is reachable from the browser and is `https://` when FreeFrame is.

### Port 80/443 already in use

```bash
# Find what's using the port
sudo lsof -i :80
# Stop that service or change the port mapping in docker-compose.prod.yml
```

### Large file uploads failing

Large media files are uploaded directly to S3 via presigned URLs (bypassing Traefik), so proxy limits don't apply to file data. If uploads still fail:
- Check that your S3 bucket doesn't have a size limit
- Verify your server has enough `/tmp` space for transcoding
- Check worker logs for processing errors
