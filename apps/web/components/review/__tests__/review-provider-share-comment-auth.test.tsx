import React from 'react'
import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, waitFor } from '@testing-library/react'
import { useReviewStore } from '@/stores/review-store'
import { ReviewProvider, useReview } from '../review-provider'

// The share endpoints read an expired access token as no token, so a signed-in
// viewer back after its 15 minutes posted as nobody and got "guest_email and
// guest_name required for anonymous comments", until opening the app refreshed
// it. The comment has to go out under a refreshed token.

const b64url = (s: string) =>
  btoa(s).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')

function accessToken(inSeconds: number, n: number): string {
  const header = b64url(JSON.stringify({ alg: 'HS256', typ: 'JWT' }))
  const exp = Math.floor(Date.now() / 1000) + inSeconds
  return `${header}.${b64url(JSON.stringify({ sub: 'user-1', type: 'access', exp, n }))}.sig`
}

const EXPIRED = accessToken(-3600, 0)
const FRESH = accessToken(900, 1)

let commentAuth: string | undefined
let refreshes = 0

beforeEach(() => {
  useReviewStore.getState().reset()
  localStorage.clear()
  localStorage.setItem('ff_access_token', EXPIRED)
  localStorage.setItem('ff_refresh_token', 'the-refresh-token')
  commentAuth = undefined
  refreshes = 0
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    if (url.endsWith('/auth/refresh')) {
      refreshes += 1
      return { ok: true, status: 200, json: async () => ({ access_token: FRESH, refresh_token: 'rotated' }) }
    }
    if (init?.method === 'POST' && url.includes('/share/tok/comment')) {
      commentAuth = (init.headers as Record<string, string>).Authorization
      return { ok: true, status: 201, json: async () => ({ id: 'c1', body: 'hi' }) }
    }
    // Mount-time reads (stream, versions, comments): not under test.
    return { ok: false, status: 404, json: async () => ({}) }
  }))
})

afterEach(() => {
  vi.unstubAllGlobals()
  localStorage.clear()
})

describe('ReviewProvider addComment on a share link, signed in with an expired token', () => {
  it('posts under a refreshed token, not as an anonymous guest', async () => {
    const wrapper = ({ children }: { children: React.ReactNode }) => (
      <ReviewProvider assetId="a1" shareToken="tok">{children}</ReviewProvider>
    )
    const { result } = renderHook(() => useReview(), { wrapper })
    await waitFor(() => expect(result.current.isLoading).toBe(false))

    await result.current.addComment({ body: 'hi' })

    expect(commentAuth).toBe(`Bearer ${FRESH}`)
    expect(refreshes).toBe(1)
    expect(localStorage.getItem('ff_access_token')).toBe(FRESH)
  })
})
