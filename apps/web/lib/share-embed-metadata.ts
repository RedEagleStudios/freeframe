import type { Metadata } from 'next'

/**
 * `GET /share/{token}/embed`: what a link preview (Discord, Slack, iMessage…)
 * may show for a share link. The API answers 404 for every link a stranger
 * must not learn anything about (password, `secure`, expired, disabled,
 * missing), which arrives here as `null`.
 *
 * `image.url` is API-relative and stable (`/share/{t}/embed.jpg`). For a video
 * it is a frame of the video with a play button drawn on it, made by the API
 * (also while the video is still processing); for an image, a redirect to a
 * fresh presigned URL of its thumbnail, so a cached embed never points at an
 * expired signature. A video is never embedded as a player: the preview is
 * that picture, and the link opens the share page in the browser.
 */
export interface ShareEmbed {
  title: string
  description: string | null
  site_name: string
  asset_type: 'video' | 'audio' | 'image' | 'image_carousel' | null
  image: { url: string; width: number | null; height: number | null } | null
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
 * - With a picture: a large-image card (`summary_large_image`).
 * - Without one: a plain summary card.
 * - `null` (the link is private, gone, or the API did not answer): the
 *   generic title and nothing else, so a preview never leaks a thumbnail.
 *
 * The picture URL is absolute against the PUBLIC API address; without one
 * (`null`) it is dropped rather than emitted relative.
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
