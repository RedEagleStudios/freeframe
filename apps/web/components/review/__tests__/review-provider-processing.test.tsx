/**
 * The review provider's part in previewing a version that is still processing.
 *
 * Project page: the selected version lives in the store as a copy, so when SSE
 * makes the page refetch the versions, a status change of that same version
 * (processing → ready) has to reach the store, or the player never refetches
 * its stream and stays on the preview.
 *
 * Share page: the pseudo asset takes its version's status from the stream
 * answer instead of assuming "ready", keeps the stream's kind, and — with no
 * event stream — asks again on a timer until the answer is HLS.
 */
import React from 'react'
import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest'
import { act, renderHook, waitFor } from '@testing-library/react'
import { useReviewStore } from '@/stores/review-store'

const { get } = vi.hoisted(() => ({ get: vi.fn() }))
vi.mock('@/lib/api', () => ({ api: { get, post: vi.fn() } }))

import { ReviewProvider, SHARE_PROCESSING_POLL_MS, useReview } from '../review-provider'

const ORIGINAL = 'https://s3.test/raw/clip.mov?X-Amz-Signature=sig'

beforeEach(() => {
  useReviewStore.getState().reset()
})
afterEach(() => {
  get.mockReset()
  vi.unstubAllGlobals()
  vi.useRealTimers()
})

describe('project page', () => {
  it('carries a status change of the selected version into the store on refetchVersions', async () => {
    let status = 'processing'
    const v1 = () => ({ id: 'v1', asset_id: 'a1', version_number: 1, processing_status: status })
    get.mockImplementation(async (url: string) => {
      if (url === '/assets/a1') {
        return { id: 'a1', name: 'Clip', asset_type: 'video', latest_version: v1() }
      }
      if (url === '/assets/a1/versions') return [v1()]
      return []
    })
    const wrapper = ({ children }: { children: React.ReactNode }) => (
      <ReviewProvider assetId="a1">{children}</ReviewProvider>
    )
    const { result } = renderHook(() => useReview(), { wrapper })
    await waitFor(() => expect(result.current.isLoading).toBe(false))

    // No ready version: the asset opens on its latest, still processing.
    expect(useReviewStore.getState().currentVersion?.processing_status).toBe('processing')

    status = 'ready'
    await act(() => result.current.refetchVersions())

    expect(useReviewStore.getState().currentVersion).toMatchObject({ id: 'v1', processing_status: 'ready' })
  })
})

describe('share page', () => {
  function respond(body: unknown, ok = true) {
    return { ok, status: ok ? 200 : 404, json: async () => body } as Response
  }

  it('marks a progressive preview as processing, then swaps to HLS once the poll says so', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    let ready = false
    const fetchMock = vi.fn(async (url: string) => {
      if (url.includes('/share/tok/stream/a1')) {
        return respond(
          ready
            ? { url: '/stream/hls/master.m3u8?token=t', kind: 'hls', processing: false, version_id: 'v1', name: 'Clip', asset_type: 'video' }
            : { url: ORIGINAL, kind: 'progressive', processing: true, version_id: 'v1', name: 'Clip', asset_type: 'video' },
        )
      }
      if (url.includes('/share/tok/assets/a1/versions')) {
        return respond(ready ? [{ id: 'v1', version_number: 1, processing_status: 'ready', created_at: '' }] : [])
      }
      if (url.includes('/share/tok/comments')) return respond([])
      return respond({}, false)
    })
    vi.stubGlobal('fetch', fetchMock)

    const wrapper = ({ children }: { children: React.ReactNode }) => (
      <ReviewProvider assetId="a1" shareToken="tok">{children}</ReviewProvider>
    )
    const { result } = renderHook(() => useReview(), { wrapper })
    await waitFor(() => expect(result.current.isLoading).toBe(false))

    expect(result.current.asset).toMatchObject({ stream_url: ORIGINAL, stream_kind: 'progressive' })
    expect(useReviewStore.getState().currentVersion?.processing_status).toBe('processing')

    // Still processing: the poll leaves the preview alone (a new presigned URL
    // would restart it).
    const streamCalls = () => fetchMock.mock.calls.filter(([u]) => String(u).includes('/stream/a1')).length
    const before = streamCalls()
    await act(() => vi.advanceTimersByTimeAsync(SHARE_PROCESSING_POLL_MS))
    expect(streamCalls()).toBe(before + 1)
    expect(result.current.asset?.stream_url).toBe(ORIGINAL)

    ready = true
    await act(() => vi.advanceTimersByTimeAsync(SHARE_PROCESSING_POLL_MS))
    await waitFor(() => expect(result.current.asset?.stream_kind).toBe('hls'))
    expect(result.current.asset?.stream_url).toBe('/stream/hls/master.m3u8?token=t')
    expect(useReviewStore.getState().currentVersion?.processing_status).toBe('ready')

    // Ready: no more polling.
    const settled = streamCalls()
    await act(() => vi.advanceTimersByTimeAsync(SHARE_PROCESSING_POLL_MS * 2))
    expect(streamCalls()).toBe(settled)
  })

  it('does not poll a link whose version is ready', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const fetchMock = vi.fn(async (url: string) => {
      if (url.includes('/share/tok/stream/a1')) {
        return respond({ url: '/stream/hls/master.m3u8?token=t', kind: 'hls', processing: false, version_id: 'v1', asset_type: 'video' })
      }
      if (url.includes('/versions')) return respond([{ id: 'v1', version_number: 1, processing_status: 'ready', created_at: '' }])
      return respond([])
    })
    vi.stubGlobal('fetch', fetchMock)
    const wrapper = ({ children }: { children: React.ReactNode }) => (
      <ReviewProvider assetId="a1" shareToken="tok">{children}</ReviewProvider>
    )
    const { result } = renderHook(() => useReview(), { wrapper })
    await waitFor(() => expect(result.current.isLoading).toBe(false))
    expect(useReviewStore.getState().currentVersion?.processing_status).toBe('ready')

    const calls = fetchMock.mock.calls.length
    await act(() => vi.advanceTimersByTimeAsync(SHARE_PROCESSING_POLL_MS * 2))
    expect(fetchMock.mock.calls.length).toBe(calls)
  })
})
