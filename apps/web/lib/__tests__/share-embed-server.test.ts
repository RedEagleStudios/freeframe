/**
 * The share page's embed read runs in `generateMetadata`, in front of the
 * page's first byte, so it must never throw: a private or dead link (404), an
 * API that is down, and an API that predates the endpoint all come back as
 * `null`, and the page falls back to generic metadata.
 */
import { describe, it, expect, vi, afterEach } from 'vitest'
import { fetchShareEmbed } from '../share-embed-server'
import { INTERNAL_API_URL } from '../branding-server'

afterEach(() => vi.unstubAllGlobals())

describe('reading a share link’s embed on the server', () => {
  it('asks the internal API address, uncached, with the token encoded', async () => {
    const body = { title: 'Clip', description: null, site_name: 'Acme', asset_type: 'video', image: null, video: null }
    const fetchMock = vi.fn(async () => ({ ok: true, json: async () => body }))
    vi.stubGlobal('fetch', fetchMock)

    await expect(fetchShareEmbed('a b/c')).resolves.toEqual(body)
    expect(fetchMock).toHaveBeenCalledWith(
      `${INTERNAL_API_URL}/share/a%20b%2Fc/embed`,
      expect.objectContaining({ cache: 'no-store', signal: expect.any(AbortSignal) }),
    )
  })

  it('returns null for a link the API will not describe (404)', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: false, status: 404, json: async () => ({}) })))
    await expect(fetchShareEmbed('tok')).resolves.toBeNull()
  })

  it('returns null when the API cannot be reached or answers garbage', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => { throw new TypeError('fetch failed') }))
    await expect(fetchShareEmbed('tok')).resolves.toBeNull()

    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, json: async () => { throw new SyntaxError('bad json') } })))
    await expect(fetchShareEmbed('tok')).resolves.toBeNull()
  })
})
