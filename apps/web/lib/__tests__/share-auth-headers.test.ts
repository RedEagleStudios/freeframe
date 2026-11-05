import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

/**
 * The public share endpoints read an expired token as no token and never
 * answer 401, so nothing refreshed it there: a signed-in viewer back after the
 * access token's 15 minutes was taken for an anonymous guest, and commenting
 * failed with "guest_email and guest_name required for anonymous comments"
 * until they opened the app, which refreshed it, and came back.
 */

const store: Record<string, string> = {}

const b64url = (s: string) =>
  btoa(s).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')

/** A JWT-shaped access token expiring `inSeconds` from now. */
function accessToken(inSeconds: number, n = 0): string {
  const header = b64url(JSON.stringify({ alg: 'HS256', typ: 'JWT' }))
  const exp = Math.floor(Date.now() / 1000) + inSeconds
  const payload = b64url(JSON.stringify({ sub: 'user-1', type: 'access', exp, n }))
  return `${header}.${payload}.sig`
}

beforeEach(() => {
  vi.resetModules()
  for (const k of Object.keys(store)) delete store[k]
  vi.stubGlobal('localStorage', {
    getItem: (k: string) => store[k] ?? null,
    setItem: (k: string, v: string) => { store[k] = v },
    removeItem: (k: string) => { delete store[k] },
  })
  vi.stubGlobal('location', { href: '' })
})
afterEach(() => vi.unstubAllGlobals())

// Imported per test after `vi.resetModules()`, as auth-refresh.test.ts does, so
// no refresh in flight carries over from the one before.
async function headers() {
  const mod = await import('../auth')
  return mod.shareAuthHeaders()
}

describe('shareAuthHeaders', () => {
  it('refreshes an expired access token before sending it', async () => {
    store['ff_access_token'] = accessToken(-60)
    store['ff_refresh_token'] = 'the-refresh-token'
    const fresh = accessToken(900, 1)
    const fetchMock = vi.fn(async () => ({
      ok: true, status: 200,
      json: async () => ({ access_token: fresh, refresh_token: 'rotated' }),
    }))
    vi.stubGlobal('fetch', fetchMock)

    await expect(headers()).resolves.toEqual({ Authorization: `Bearer ${fresh}` })
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(String((fetchMock.mock.calls[0] as unknown[])[0])).toMatch(/\/auth\/refresh$/)
    expect(store['ff_access_token']).toBe(fresh)
  })

  it('refreshes one about to expire, which could lapse on the way', async () => {
    store['ff_access_token'] = accessToken(5)
    store['ff_refresh_token'] = 'the-refresh-token'
    const fresh = accessToken(900, 1)
    vi.stubGlobal('fetch', vi.fn(async () => ({
      ok: true, status: 200,
      json: async () => ({ access_token: fresh, refresh_token: 'rotated' }),
    })))

    await expect(headers()).resolves.toEqual({ Authorization: `Bearer ${fresh}` })
  })

  it('sends a live access token as it is', async () => {
    const live = accessToken(600)
    store['ff_access_token'] = live
    store['ff_refresh_token'] = 'the-refresh-token'
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)

    await expect(headers()).resolves.toEqual({ Authorization: `Bearer ${live}` })
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('ends a refused session without leaving the share page', async () => {
    store['ff_access_token'] = accessToken(-60)
    store['ff_refresh_token'] = 'the-refresh-token'
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: false, status: 401 })))

    await expect(headers()).resolves.toEqual({})
    expect(store['ff_access_token']).toBeUndefined()
    expect(store['ff_refresh_token']).toBeUndefined()
    // Sent to /login, the viewer would lose the link; they go on as a guest.
    expect(location.href).toBe('')
  })

  it('keeps the session when the refresh gets no answer, and sends nothing', async () => {
    store['ff_access_token'] = accessToken(-60)
    store['ff_refresh_token'] = 'the-refresh-token'
    vi.stubGlobal('fetch', vi.fn(async () => { throw new TypeError('Failed to fetch') }))

    await expect(headers()).resolves.toEqual({})
    expect(store['ff_refresh_token']).toBe('the-refresh-token')
    expect(location.href).toBe('')
  })

  it('sends nothing for a guest', async () => {
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)

    await expect(headers()).resolves.toEqual({})
    expect(fetchMock).not.toHaveBeenCalled()
  })
})
