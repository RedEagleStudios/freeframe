/**
 * A video version still processing plays a preview of its original.
 *
 * The stream endpoint answers `kind: "progressive"` for it, with a presigned
 * GET of the upload: that is a plain `<video src>`, never hls.js, and the
 * player says it is a preview. When the browser cannot decode the original
 * (ProRes, most HEVC) or the endpoint has nothing to offer (409), the player
 * gives way to the processing placeholder. Once the version is ready the
 * status change alone makes the player fetch again, and it moves to HLS.
 *
 * The real player hook runs here; only hls.js and the API are replaced.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import * as React from 'react'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { useReviewStore } from '@/stores/review-store'
import type { AssetVersion } from '@/types'

const hls = vi.hoisted(() => {
  const instances: Array<{ src: string | null }> = []
  class FakeHls {
    static Events = { MANIFEST_PARSED: 'm', ERROR: 'e', LEVEL_SWITCHED: 'l', FRAG_BUFFERED: 'f' }
    static isSupported = () => true
    src: string | null = null
    bandwidthEstimate = 0
    constructor() { instances.push(this) }
    on() {}
    loadSource(src: string) { this.src = src }
    attachMedia() {}
    destroy() {}
  }
  return { FakeHls, instances }
})
vi.mock('hls.js', () => ({ default: hls.FakeHls, Events: hls.FakeHls.Events }))

const { get } = vi.hoisted(() => ({ get: vi.fn() }))
vi.mock('@/lib/api', () => ({ api: { get } }))
vi.mock('../review-provider', () => ({
  useReview: () => ({ registerPauseHandler: () => {}, registerTogglePlayHandler: () => () => {} }),
}))

import { VideoPlayer } from '../video-player'

const API = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'
const ORIGINAL = 'https://s3.test/raw/clip.mp4?X-Amz-Signature=sig'

function version(status: AssetVersion['processing_status']): AssetVersion {
  return {
    id: 'v2', asset_id: 'a1', version_number: 2, processing_status: status,
    created_by: 'u1', created_at: '', deleted_at: null,
  }
}

vi.stubGlobal('matchMedia', (query: string) => ({
  matches: true, media: query, onchange: null,
  addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {},
  dispatchEvent: () => false,
}))

beforeEach(() => {
  vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} })
  hls.instances.length = 0
  useReviewStore.getState().reset()
  useReviewStore.getState().setCurrentVersion(version('processing'))
})
afterEach(() => {
  get.mockReset()
})

const video = () => document.querySelector('video') as HTMLVideoElement

describe('a processing video version', () => {
  it('plays a progressive stream through <video src>, without hls.js, and says it is a preview', async () => {
    get.mockResolvedValue({ url: ORIGINAL, kind: 'progressive', processing: true })
    render(<VideoPlayer assetId="a1" />)

    await waitFor(() => expect(video().getAttribute('src')).toBe(ORIGINAL))
    expect(get).toHaveBeenCalledWith('/assets/a1/stream?version_id=v2')
    expect(hls.instances).toHaveLength(0)
    // No hidden hover-preview video downloading the whole original again.
    expect(document.querySelectorAll('video')).toHaveLength(1)
    expect(screen.getByText('Preview — still processing')).toBeInTheDocument()
    expect(screen.queryByText('Processing asset')).toBeNull()
  })

  it('falls back to the processing placeholder, with the progress, when the original will not decode', async () => {
    get.mockResolvedValue({ url: ORIGINAL, kind: 'progressive', processing: true })
    render(<VideoPlayer assetId="a1" processingPercent={42.4} />)
    await waitFor(() => expect(video().getAttribute('src')).toBe(ORIGINAL))

    fireEvent.error(video())

    expect(await screen.findByText('Processing asset')).toBeInTheDocument()
    expect(screen.getByText('42% processed')).toBeInTheDocument()
    expect(screen.queryByText('Preview — still processing')).toBeNull()
    // The codec failure is not reported as a playback error.
    expect(screen.queryByText('Video playback error')).toBeNull()
  })

  it('shows the placeholder when the stream endpoint has nothing to preview (409)', async () => {
    get.mockRejectedValue(Object.assign(new Error('Version not ready'), { status: 409 }))
    render(<VideoPlayer assetId="a1" />)

    expect(await screen.findByText('Processing asset')).toBeInTheDocument()
    expect(hls.instances).toHaveLength(0)
  })

  it('refetches the stream when the same version turns ready, and switches to HLS', async () => {
    get.mockResolvedValueOnce({ url: ORIGINAL, kind: 'progressive', processing: true })
    render(<VideoPlayer assetId="a1" />)
    await waitFor(() => expect(video().getAttribute('src')).toBe(ORIGINAL))

    get.mockResolvedValueOnce({ url: '/stream/hls/master.m3u8?token=t', kind: 'hls', processing: false })
    act(() => useReviewStore.getState().setCurrentVersion(version('ready')))

    // The player and the scrubber's frame preview, both on the HLS stream.
    await waitFor(() => expect(hls.instances.length).toBeGreaterThan(0))
    expect(get).toHaveBeenCalledTimes(2)
    expect(get).toHaveBeenLastCalledWith('/assets/a1/stream?version_id=v2')
    expect(hls.instances.every((i) => i.src === `${API}/stream/hls/master.m3u8?token=t`)).toBe(true)
    expect(screen.queryByText('Preview — still processing')).toBeNull()
  })

  it('picks hls.js from kind, not from the URL', async () => {
    useReviewStore.getState().setCurrentVersion(version('ready'))
    get.mockResolvedValue({ url: '/stream/hls/master?token=t', kind: 'hls' })
    render(<VideoPlayer assetId="a1" />)

    await waitFor(() => expect(hls.instances.length).toBeGreaterThan(0))
    expect(hls.instances.every((i) => i.src === `${API}/stream/hls/master?token=t`)).toBe(true)
  })

  it('takes the kind of a pre-fetched share stream', async () => {
    render(<VideoPlayer assetId="a1" initialStreamUrl={ORIGINAL} initialStreamKind="progressive" />)

    await waitFor(() => expect(video().getAttribute('src')).toBe(ORIGINAL))
    expect(get).not.toHaveBeenCalled()
    expect(hls.instances).toHaveLength(0)
    expect(screen.getByText('Preview — still processing')).toBeInTheDocument()
  })
})
