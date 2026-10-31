import type { Metadata } from 'next'
import { headers } from 'next/headers'
import { getServerBranding } from '@/lib/branding-server'
import { buildShareEmbedMetadata, resolvePublicApiUrl } from '@/lib/share-embed-metadata'
import { fetchShareEmbed } from '@/lib/share-embed-server'

/**
 * Link previews for a share link (Discord, Slack, iMessage, X…). The page
 * itself is a client component, so its metadata lives here, resolved on the
 * server before the first byte. Next holds the response for the metadata only
 * for crawler user agents (Discordbot among them) and streams it to browsers.
 */
export async function generateMetadata({
  params,
}: {
  params: Promise<{ token: string }>
}): Promise<Metadata> {
  const { token } = await params
  const [embed, branding, requestHeaders] = await Promise.all([
    fetchShareEmbed(token),
    getServerBranding(),
    headers(),
  ])
  const host = requestHeaders.get('x-forwarded-host') ?? requestHeaders.get('host')
  const proto = requestHeaders.get('x-forwarded-proto')?.split(',')[0]?.trim() || 'https'
  const publicApiUrl = resolvePublicApiUrl(process.env.NEXT_PUBLIC_API_URL, {
    frontendUrl: process.env.FRONTEND_URL,
    requestOrigin: host ? `${proto}://${host}` : null,
  })
  return buildShareEmbedMetadata(embed, publicApiUrl, branding?.org_name || 'FreeFrame')
}

export default function ShareTokenLayout({ children }: { children: React.ReactNode }) {
  return children
}
