import { INTERNAL_API_URL } from '@/lib/branding-server'
import type { ShareEmbed } from '@/lib/share-embed-metadata'

/**
 * A crawler gives up on a slow page, and this sits in front of the share
 * page's first byte for every visitor, so it is shorter than branding's.
 */
const TIMEOUT_MS = 2000

/**
 * A share link's embed description, fetched server-to-server (see
 * `INTERNAL_API_URL`). Null for every failure: a private or dead link (404),
 * an API that is down, slow, or predates the endpoint. The share page then
 * falls back to generic metadata and renders exactly as it did before.
 */
export async function fetchShareEmbed(token: string): Promise<ShareEmbed | null> {
  try {
    const res = await fetch(`${INTERNAL_API_URL}/share/${encodeURIComponent(token)}/embed`, {
      // A link can be disabled, expired or re-pointed at any moment; a cached
      // preview would keep showing what it no longer shares.
      cache: 'no-store',
      signal: AbortSignal.timeout(TIMEOUT_MS),
    })
    if (!res.ok) return null
    return (await res.json()) as ShareEmbed
  } catch {
    return null
  }
}
