import { describe, it, expect } from 'vitest'
import { buildShareEmbedMetadata, resolvePublicApiUrl, type ShareEmbed } from '../share-embed-metadata'

// Link previews (Discord, Slack, X…) for a share link. A video is a large
// image card (a frame with a play button, drawn by the API), never a player.
// Every URL is absolute against the API's PUBLIC address and is the API's
// stable path, never a presigned one that expires.

const API = 'https://review.example.com/api'

const VIDEO: ShareEmbed = {
  title: 'Launch film — final',
  description: 'Cut 7 for client review',
  site_name: 'Acme Review',
  asset_type: 'video',
  image: { url: '/share/tok/embed.jpg', width: 1280, height: 720 },
}

describe('buildShareEmbedMetadata', () => {
  it('gives a single-video link a large-image card and no player or og:video', () => {
    const md = buildShareEmbedMetadata(VIDEO, API)
    expect(md).toEqual({
      title: 'Launch film — final',
      description: 'Cut 7 for client review',
      openGraph: {
        type: 'website',
        title: 'Launch film — final',
        siteName: 'Acme Review',
        description: 'Cut 7 for client review',
        images: [{ url: `${API}/share/tok/embed.jpg`, width: 1280, height: 720 }],
      },
      twitter: {
        card: 'summary_large_image',
        title: 'Launch film — final',
        description: 'Cut 7 for client review',
        images: [`${API}/share/tok/embed.jpg`],
      },
    })
  })

  it('leaves out sizes it does not know', () => {
    const md = buildShareEmbedMetadata({ ...VIDEO, image: { url: '/share/tok/embed.jpg', width: null, height: null } }, API)
    expect(md.openGraph).toMatchObject({ images: [{ url: `${API}/share/tok/embed.jpg` }] })
    expect(md.openGraph).not.toHaveProperty('images.0.width')
  })

  it('clears the root layout description when the link has none', () => {
    expect(buildShareEmbedMetadata({ ...VIDEO, description: null }, API).description).toBeNull()
  })

  it('gives a folder link with no picture a plain summary card', () => {
    const md = buildShareEmbedMetadata({ ...VIDEO, title: 'Dailies', asset_type: null, image: null }, API)
    expect(md.openGraph).toEqual({ type: 'website', title: 'Dailies', siteName: 'Acme Review', description: 'Cut 7 for client review' })
    expect(md.twitter).toEqual({ card: 'summary', title: 'Dailies', description: 'Cut 7 for client review' })
  })

  it('gives a private, dead or unanswered link the generic title and no media tags', () => {
    expect(buildShareEmbedMetadata(null, API, 'Acme Review')).toEqual({ title: 'Acme Review' })
    expect(buildShareEmbedMetadata(null, API)).toEqual({ title: 'FreeFrame' })
  })

  it('drops the picture rather than emitting a relative URL when there is no public API address', () => {
    const md = buildShareEmbedMetadata(VIDEO, null)
    expect(md.openGraph).not.toHaveProperty('images')
    expect(md.twitter).toMatchObject({ card: 'summary' })
  })

  it('keeps an absolute media URL from the API as it is', () => {
    const md = buildShareEmbedMetadata(
      { ...VIDEO, image: { url: 'https://cdn.example/embed.jpg', width: null, height: null } },
      API,
    )
    expect(md.openGraph).toMatchObject({ images: [{ url: 'https://cdn.example/embed.jpg' }] })
    expect(md.openGraph).not.toHaveProperty('images.0.width')
  })
})

describe('resolvePublicApiUrl', () => {
  it('uses an absolute NEXT_PUBLIC_API_URL as it is, without a trailing slash', () => {
    expect(resolvePublicApiUrl('https://api.example.com/', { frontendUrl: 'https://app.example.com' })).toBe('https://api.example.com')
  })

  it('anchors a relative one (`/api`, as the prod compose builds it) to FRONTEND_URL’s origin', () => {
    expect(resolvePublicApiUrl('/api', { frontendUrl: 'https://review.example.com/freeframe', requestOrigin: 'http://web:3000' }))
      .toBe('https://review.example.com/api')
  })

  it('falls back to the request’s origin without FRONTEND_URL', () => {
    expect(resolvePublicApiUrl('/api', { frontendUrl: undefined, requestOrigin: 'https://review.example.com' }))
      .toBe('https://review.example.com/api')
  })

  it('gives up on a relative one with no origin to anchor it', () => {
    expect(resolvePublicApiUrl('/api', {})).toBeNull()
  })
})
