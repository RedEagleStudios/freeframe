import { describe, it, expect } from 'vitest'
import {
  buildShareEmbedMetadata,
  resolvePublicApiUrl,
  type ShareEmbed,
} from '../share-embed-metadata'

// Link previews (Discord, Slack, X…) for a share link. Discord plays og:video
// inline; the Twitter player tags are there for everything that reads those
// instead. Every URL is absolute against the API's PUBLIC address and is the
// API's stable redirecting path, never a presigned one that expires.

const API = 'https://review.example.com/api'

const VIDEO: ShareEmbed = {
  title: 'Launch film — final',
  description: 'Cut 7 for client review',
  site_name: 'Acme Review',
  asset_type: 'video',
  image: { url: '/share/tok/embed.jpg', width: 1280, height: 720 },
  video: {
    url: '/share/tok/embed.mp4',
    secure_url: '/share/tok/embed.mp4',
    type: 'video/mp4',
    width: 1920,
    height: 1080,
  },
}

describe('buildShareEmbedMetadata', () => {
  it('gives a single-video link the full og:video and twitter player tags', () => {
    expect(buildShareEmbedMetadata(VIDEO, API)).toEqual({
      title: 'Launch film — final',
      description: 'Cut 7 for client review',
      openGraph: {
        type: 'video.other',
        title: 'Launch film — final',
        siteName: 'Acme Review',
        description: 'Cut 7 for client review',
        images: [{ url: `${API}/share/tok/embed.jpg`, width: 1280, height: 720 }],
        videos: [{
          url: `${API}/share/tok/embed.mp4`,
          secureUrl: `${API}/share/tok/embed.mp4`,
          type: 'video/mp4',
          width: 1920,
          height: 1080,
        }],
      },
      twitter: {
        card: 'player',
        title: 'Launch film — final',
        description: 'Cut 7 for client review',
        images: [`${API}/share/tok/embed.jpg`],
        players: [{
          playerUrl: `${API}/share/tok/embed.mp4`,
          streamUrl: `${API}/share/tok/embed.mp4`,
          width: 1920,
          height: 1080,
        }],
      },
      other: { 'twitter:player:stream:content_type': 'video/mp4' },
    })
  })

  it('leaves out sizes it does not know, but always sizes the twitter player', () => {
    const md = buildShareEmbedMetadata(
      { ...VIDEO, description: null, image: null, video: { ...VIDEO.video!, width: null, height: null } },
      API,
    )
    expect(md.openGraph).toMatchObject({
      videos: [{ url: `${API}/share/tok/embed.mp4`, secureUrl: `${API}/share/tok/embed.mp4`, type: 'video/mp4' }],
    })
    expect(md.openGraph).not.toHaveProperty('videos.0.width')
    expect(md.openGraph).not.toHaveProperty('images')
    expect(md.openGraph).not.toHaveProperty('description')
    // Null clears the root layout's description instead of inheriting it.
    expect(md.description).toBeNull()
    expect(md.twitter).toMatchObject({ players: [{ width: 1280, height: 720 }] })
    expect(md.twitter).not.toHaveProperty('images')
  })

  it('gives a still-processing video its original as og:video, with the original’s type', () => {
    // What the API answers while the version transcodes: the early poster
    // frame, the probed portrait size, and the uploaded .mov as the video.
    const md = buildShareEmbedMetadata(
      {
        ...VIDEO,
        image: { url: '/share/tok/embed.jpg', width: 1080, height: 1920 },
        video: { ...VIDEO.video!, type: 'video/quicktime', width: 1080, height: 1920 },
      },
      API,
    )
    expect(md.openGraph).toMatchObject({
      type: 'video.other',
      images: [{ url: `${API}/share/tok/embed.jpg`, width: 1080, height: 1920 }],
      videos: [{ url: `${API}/share/tok/embed.mp4`, type: 'video/quicktime', width: 1080, height: 1920 }],
    })
    expect(md.twitter).toMatchObject({ card: 'player', players: [{ width: 1080, height: 1920 }] })
    expect(md.other).toEqual({ 'twitter:player:stream:content_type': 'video/quicktime' })
  })

  it('gives a link without a web MP4 a website card with its picture', () => {
    const md = buildShareEmbedMetadata(
      { ...VIDEO, title: 'Stills', asset_type: 'image', description: null, video: null },
      API,
    )
    expect(md).toEqual({
      title: 'Stills',
      description: null,
      openGraph: {
        type: 'website',
        title: 'Stills',
        siteName: 'Acme Review',
        images: [{ url: `${API}/share/tok/embed.jpg`, width: 1280, height: 720 }],
      },
      twitter: {
        card: 'summary_large_image',
        title: 'Stills',
        images: [`${API}/share/tok/embed.jpg`],
      },
    })
    expect(md).not.toHaveProperty('other')
  })

  it('gives a folder link with no picture a plain summary card', () => {
    const md = buildShareEmbedMetadata(
      { ...VIDEO, title: 'Dailies', asset_type: null, image: null, video: null },
      API,
    )
    expect(md.openGraph).toEqual({ type: 'website', title: 'Dailies', siteName: 'Acme Review', description: 'Cut 7 for client review' })
    expect(md.twitter).toEqual({ card: 'summary', title: 'Dailies', description: 'Cut 7 for client review' })
  })

  it('gives a private, dead or unanswered link the generic title and no media tags', () => {
    expect(buildShareEmbedMetadata(null, API, 'Acme Review')).toEqual({ title: 'Acme Review' })
    expect(buildShareEmbedMetadata(null, API)).toEqual({ title: 'FreeFrame' })
  })

  it('drops media rather than emitting a relative URL when there is no public API address', () => {
    const md = buildShareEmbedMetadata(VIDEO, null)
    expect(md.openGraph).toMatchObject({ type: 'website' })
    expect(md.openGraph).not.toHaveProperty('videos')
    expect(md.openGraph).not.toHaveProperty('images')
    expect(md).not.toHaveProperty('other')
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
