import type { Metadata } from 'next'

/**
 * `GET /share/{token}/embed`: what a link preview (Discord, Slack, iMessage…)
 * may show for a share link. The API answers 404 for every link a stranger
 * must not learn anything about (password, `secure`, expired, disabled,
 * missing), which arrives here as `null`.
 *
 * `image.url` / `video.url` are API-relative and stable (`/share/{t}/embed.jpg`,
 * `/share/{t}/embed.mp4`); they redirect to a fresh presigned URL on each
 * request, so a cached embed never points at an expired signature.
 *
 * While the video is still processing, `video` is its uploaded original (when
 * a browser can play it), so `video.type` is that file's type, which is not
 * always MP4; its sizes are the probe's, or null before the probe has run.
 */
export interface ShareEmbed {
  title: string
  description: string | null
  site_name: string
  asset_type: 'video' | 'audio' | 'image' | 'image_carousel' | null
  image: { url: string; width: number | null; height: number | null } | null
  video: {
    url: string
    secure_url: string
    type: string
    width: number | null
    height: number | null
  } | null
}

/**
 * The video tags a share page renders itself (see the share layout): Next's
 * `openGraph.videos` always writes `og:video` + `og:video:type` + the Twitter
 * player tags, and with those Discord sizes the file and refuses anything over
 * its embed limit (a 272 MB MP4 got no player). The minimal set below --
 * `og:video:url` plus its size, beside `og:type=video.other` and `og:image` --
 * is what large-file embedders use, and Discord plays it inline whatever the
 * size. Verified against Discord with the same 272 MB file.
 */
export function shareEmbedVideoTags(
  embed: ShareEmbed | null,
  publicApiUrl: string | null,
): { property: string; content: string }[] {
  const video = embed?.video
  if (!video || !publicApiUrl) return []
  return [
    { property: 'og:video:url', content: absolute(publicApiUrl, video.url) },
    ...(video.width ? [{ property: 'og:video:width', content: String(video.width) }] : []),
    ...(video.height ? [{ property: 'og:video:height', content: String(video.height) }] : []),
  ]
}

const ABSOLUTE_HTTP_URL = /^https?:\/\//i

function originOf(url: string | undefined | null): string | null {
  if (!url) return null
  try {
    return new URL(url).origin
  } catch {
    return null
  }
}

/**
 * The API's public address, as a crawler outside the deployment reaches it.
 *
 * `NEXT_PUBLIC_API_URL` is written for the browser and is often relative: the
 * production compose builds the web image with `/api`, which the browser
 * resolves against the page's origin. A crawler reading a meta tag resolves
 * nothing, so a relative value is anchored here to `FRONTEND_URL`'s origin, or
 * failing that to the origin the request came in on. Never the internal
 * (`API_INTERNAL_URL`) address, which only the web container can reach.
 */
export function resolvePublicApiUrl(
  publicApiUrl: string | undefined,
  context: { frontendUrl?: string | null; requestOrigin?: string | null },
): string | null {
  const raw = (publicApiUrl || 'http://localhost:8000').trim()
  if (ABSOLUTE_HTTP_URL.test(raw)) return raw.replace(/\/+$/, '')
  const origin = originOf(context.frontendUrl) ?? originOf(context.requestOrigin)
  if (!origin) return null
  const path = raw.startsWith('/') ? raw : `/${raw}`
  return `${origin}${path}`.replace(/\/+$/, '')
}

function absolute(apiBase: string, path: string): string {
  if (ABSOLUTE_HTTP_URL.test(path)) return path
  return `${apiBase}${path.startsWith('/') ? path : `/${path}`}`
}

/**
 * Maps a share link's embed description to page metadata.
 *
 * - A single-video link with a web MP4 (or, while it processes, a playable
 *   original): `og:type=video.other` and a `player` card; the video itself is
 *   in the tags from `shareEmbedVideoTags`, which the share layout renders.
 * - Anything else with a picture: `og:type=website` and a large-image card.
 * - `null` (the link is private, gone, or the API did not answer): the
 *   generic title and nothing else, so a preview never leaks a thumbnail.
 *
 * Every URL is absolute against the PUBLIC API address; without one (`null`)
 * the media tags are dropped rather than emitted relative.
 */
export function buildShareEmbedMetadata(
  embed: ShareEmbed | null,
  publicApiUrl: string | null,
  fallbackTitle = 'FreeFrame',
): Metadata {
  if (!embed) return { title: fallbackTitle }

  const title = embed.title || fallbackTitle
  const description = embed.description || null
  const image =
    embed.image && publicApiUrl
      ? {
          url: absolute(publicApiUrl, embed.image.url),
          ...(embed.image.width ? { width: embed.image.width } : {}),
          ...(embed.image.height ? { height: embed.image.height } : {}),
        }
      : null
  const video = embed.video && publicApiUrl ? embed.video : null

  if (video && publicApiUrl) {
    return {
      title,
      description,
      openGraph: {
        type: 'video.other',
        title,
        siteName: embed.site_name,
        ...(description ? { description } : {}),
        ...(image ? { images: [image] } : {}),
      },
      other: { 'twitter:card': 'player' },
    }
  }

  return {
    title,
    description,
    openGraph: {
      type: 'website',
      title,
      siteName: embed.site_name,
      ...(description ? { description } : {}),
      ...(image ? { images: [image] } : {}),
    },
    twitter: {
      card: image ? 'summary_large_image' : 'summary',
      title,
      ...(description ? { description } : {}),
      ...(image ? { images: [image.url] } : {}),
    },
  }
}
