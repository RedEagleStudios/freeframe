/**
 * A share link to a video with no ready version yet previews the newest
 * version still processing: the share stream answers `kind: "progressive"`,
 * `processing: true`. The screen plays it with the same badge and the same
 * media-error fallback as the project page. With nothing to preview (404) it
 * shows the processing placeholder rather than a spinner that never ends.
 *
 * ShareReviewScreen, ReviewProvider, VideoPlayer and its hook are all real;
 * only the network and the viewport are faked.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { useReviewStore } from '@/stores/review-store'
import { ShareReviewScreen } from '../share-review-screen'

const ORIGINAL = 'https://s3.test/raw/clip.mov?X-Amz-Signature=sig'

function respond(status: number, body: unknown) {
  return { ok: status >= 200 && status < 300, status, json: async () => body }
}

let stream: { status: number; body: unknown }

beforeEach(() => {
  useReviewStore.getState().reset()
  stream = {
    status: 200,
    body: { name: 'Clip.mov', asset_type: 'video', url: ORIGINAL, kind: 'progressive', processing: true, version_id: 'v1' },
  }
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    if (url.includes('/share/tok/stream/a1')) return respond(stream.status, stream.body)
    // The guest sees ready versions only, and there are none yet.
    if (url.includes('/share/tok/assets/a1/versions')) return respond(200, [])
    if (url.includes('/share/tok/comments')) return respond(200, [])
    return respond(404, {})
  }))
  vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} })
  vi.stubGlobal('matchMedia', (query: string) => ({
    matches: true, media: query, onchange: null,
    addEventListener() {}, removeEventListener() {},
    addListener() {}, removeListener() {}, dispatchEvent: () => false,
  }))
  Element.prototype.scrollIntoView = vi.fn()
})

afterEach(() => {
  vi.unstubAllGlobals()
})

function open() {
  return render(
    <ShareReviewScreen
      token="tok" assetId="a1" assetName="Clip.mov"
      permission="comment" allowDownload={false} showVersions={false}
    />,
  )
}

describe('a share link to a video still processing', () => {
  it('plays the progressive preview with the badge', async () => {
    const view = open()
    await waitFor(() => expect(view.container.querySelector('video')?.getAttribute('src')).toBe(ORIGINAL), { timeout: 3000 })
    expect(screen.getByText('Preview — still processing')).toBeInTheDocument()
  })

  it('falls back to the processing placeholder when the original will not decode', async () => {
    const view = open()
    await waitFor(() => expect(view.container.querySelector('video')?.getAttribute('src')).toBe(ORIGINAL), { timeout: 3000 })

    fireEvent.error(view.container.querySelector('video') as HTMLVideoElement)

    expect(await screen.findByText('Processing asset')).toBeInTheDocument()
    expect(screen.queryByText('Preview — still processing')).toBeNull()
  })

  it('shows the processing placeholder when there is nothing to preview yet', async () => {
    stream = { status: 404, body: { detail: 'Not found' } }
    open()
    expect(await screen.findByText('Processing asset', {}, { timeout: 3000 })).toBeInTheDocument()
    expect(document.querySelector('video')).toBeNull()
  })
})
