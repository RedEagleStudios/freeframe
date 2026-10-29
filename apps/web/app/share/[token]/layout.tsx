import type { Metadata } from 'next'
import { headers } from 'next/headers'
import { cache } from 'react'
import { getServerBranding } from '@/lib/branding-server'
import { buildShareEmbedMetadata, resolvePublicApiUrl, shareEmbedVideoTags } from '@/lib/share-embed-metadata'
import { fetchShareEmbed } from '@/lib/share-embed-server'

/**
 * Link previews for a share link (Discord, Slack, iMessage, X…). The page
 * itself is a client component, so its metadata lives here, resolved on the
 * server before the first byte. Next holds the response for the metadata only
 * for crawler user agents (Discordbot among them) and streams it to browsers.
 */

// generateMetadata and the layout both need it; one API call per request.
const embedFor = cache(fetchShareEmbed)

async function publicApiUrl(): Promise<string | null> {
  const requestHeaders = await headers()
  const host = requestHeaders.get('x-forwarded-host') ?? requestHeaders.get('host')
  const proto = requestHeaders.get('x-forwarded-proto')?.split(',')[0]?.trim() || 'https'
  return resolvePublicApiUrl(process.env.NEXT_PUBLIC_API_URL, {
    frontendUrl: process.env.FRONTEND_URL,
    requestOrigin: host ? `${proto}://${host}` : null,
  })
}

export async function generateMetadata({
  params,
}: {
  params: Promise<{ token: string }>
}): Promise<Metadata> {
  const { token } = await params
  const [embed, branding, apiUrl] = await Promise.all([embedFor(token), getServerBranding(), publicApiUrl()])
  return buildShareEmbedMetadata(embed, apiUrl, branding?.org_name || 'FreeFrame')
}

export default async function ShareTokenLayout({
  children,
  params,
}: {
  children: React.ReactNode
  params: Promise<{ token: string }>
}) {
  const { token } = await params
  const [embed, apiUrl] = await Promise.all([embedFor(token), publicApiUrl()])
  // Rendered here rather than through `metadata` because Next has no way to
  // write `og:video:url` without also writing `og:video:type` (see
  // shareEmbedVideoTags). React hoists these into <head>.
  return (
    <>
      {shareEmbedVideoTags(embed, apiUrl).map((tag) => (
        <meta key={tag.property} property={tag.property} content={tag.content} />
      ))}
      {children}
    </>
  )
}
