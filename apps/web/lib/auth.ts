import { withBasePath } from './base-path'
import { expiresWithin, subjectOf } from './jwt-subject'

const ACCESS_TOKEN_KEY = 'ff_access_token'
const REFRESH_TOKEN_KEY = 'ff_refresh_token'

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'

export function getAccessToken(): string | null {
  if (typeof window === 'undefined') return null
  return localStorage.getItem(ACCESS_TOKEN_KEY)
}

export function getRefreshToken(): string | null {
  if (typeof window === 'undefined') return null
  return localStorage.getItem(REFRESH_TOKEN_KEY)
}

export function setTokens(access: string, refresh: string): void {
  if (typeof window === 'undefined') return
  localStorage.setItem(ACCESS_TOKEN_KEY, access)
  localStorage.setItem(REFRESH_TOKEN_KEY, refresh)
  // Set cookies so the proxy can check auth on server side
  document.cookie = `${ACCESS_TOKEN_KEY}=${access}; path=/; max-age=${60 * 60 * 24 * 7}; SameSite=Lax`
  document.cookie = `${REFRESH_TOKEN_KEY}=${refresh}; path=/; max-age=${60 * 60 * 24 * 7}; SameSite=Lax`
}

/**
 * Set when the session is ended on purpose, so the leave-page warning
 * (`hooks/use-upload-leave-guard.ts`) stays quiet for the navigation to /login
 * that follows. Never cleared in a real page: the next thing that happens to
 * this document is that it goes away. If it does not (the load is aborted, or
 * the page comes back from the back/forward cache), `isLeaveGuardSuppressed`
 * stops honouring it as soon as there is a session again.
 */
let leaveGuardSuppressed = false

/**
 * Whether the browser must not be asked to prompt for the navigation in
 * progress: the session was ended on purpose and has not come back since. The
 * prompt exists for a session worth keeping the page for, and with a token in
 * storage again, signed in on this document or in another tab, there is one.
 */
export function isLeaveGuardSuppressed(): boolean {
  return leaveGuardSuppressed && getAccessToken() === null
}

/** Exported for tests. */
export function resetLeaveGuardSuppression(): void {
  leaveGuardSuppressed = false
}

const sessionEndListeners = new Set<() => void>()

/**
 * Calls `cb` when this tab ends the session (`clearTokens`), after the tokens
 * are gone and before the page starts to leave. A tab is never sent the
 * `storage` event for its own writes, so this is the only way code in this tab
 * hears of it in time. Returns the unsubscribe function.
 */
export function onSessionEnded(cb: () => void): () => void {
  sessionEndListeners.add(cb)
  return () => {
    sessionEndListeners.delete(cb)
  }
}

/**
 * Ends the session in this tab: the tokens go and the page loads /login.
 *
 * Every caller means it. A refresh the server refused, or one with no token to
 * send, ends the session by force, and a Log out has already asked about the
 * uploads it would stop (`components/layout/sidebar.tsx`). Either way the
 * tokens are gone before the page leaves, so the browser's "Leave site?"
 * prompt has nothing to offer: "Stay" would leave a tab with no session that
 * signs itself out again on the next request. The flag is set before the
 * navigation, every time.
 *
 * `onSessionEnded` listeners run once the tokens are gone and before the
 * navigation: the upload store saves the files it holds only in memory. A
 * listener that throws does not keep the page from leaving.
 */
export function clearTokens(): void {
  endSession(true)
}

/**
 * `clearTokens`, with the navigation to /login only when `leave` is set. A share
 * page ends a dead session without leaving (`getFreshAccessToken`): its viewer
 * carries on as a guest, and sending them to /login would lose the link.
 */
function endSession(leave: boolean): void {
  if (typeof window === 'undefined') return
  if (leave) leaveGuardSuppressed = true
  localStorage.removeItem(ACCESS_TOKEN_KEY)
  localStorage.removeItem(REFRESH_TOKEN_KEY)
  // Clear auth cookies
  document.cookie = `${ACCESS_TOKEN_KEY}=; path=/; max-age=0`
  document.cookie = `${REFRESH_TOKEN_KEY}=; path=/; max-age=0`
  sessionEndListeners.forEach((cb) => {
    try {
      cb()
    } catch (err) {
      console.warn('A listener failed as the session ended', err)
    }
  })
  if (leave) window.location.href = withBasePath('/login')
}

// Deduplicate concurrent refresh calls — when access token expires, multiple
// API calls may simultaneously get 401 and try to refresh. Only one should run,
// and a caller that joins one already out gets that one's handling of a
// refusal.
let _refreshPromise: Promise<string | null> | null = null

/**
 * Trades the refresh token for a new access token; null when it could not.
 * A refusal ends the session, and by default leaves for /login; with
 * `leaveOnRefusal: false` the page stays where it is.
 */
export async function refreshAccessToken(
  { leaveOnRefusal = true }: { leaveOnRefusal?: boolean } = {},
): Promise<string | null> {
  if (_refreshPromise) return _refreshPromise

  _refreshPromise = _doRefresh(leaveOnRefusal)
  try {
    return await _refreshPromise
  } finally {
    _refreshPromise = null
  }
}

// A token this close to expiry is refreshed before it is sent: it could expire
// on the way.
const EXPIRY_SKEW_MS = 30_000

/**
 * The access token to send to an endpoint that answers anyone but acts on a
 * signed-in viewer when there is one: the public share endpoints. Refreshed
 * first when it has expired or is about to; null when there is no session, or
 * the refresh failed.
 *
 * Those endpoints never answer 401. They read an expired token as no token, so
 * the refresh-on-401 in `lib/api.ts` never runs, and a signed-in viewer whose
 * access token had lapsed (it lives 15 minutes) was taken for an anonymous
 * guest: a comment failed with "guest_email and guest_name required", and a
 * secure link asked them to sign in. Opening the app first used to be the only
 * way to get a fresh one.
 *
 * A refused refresh ends the session without leaving the page, so the viewer
 * carries on as a guest rather than lose the link to /login.
 */
export async function getFreshAccessToken(): Promise<string | null> {
  const token = getAccessToken()
  if (!token || !expiresWithin(token, EXPIRY_SKEW_MS)) return token
  return refreshAccessToken({ leaveOnRefusal: false })
}

/**
 * `getFreshAccessToken` as request headers: `Authorization` when there is a
 * token, none when there is not or storage cannot be read (a sandboxed frame),
 * which the share endpoints answer as a guest.
 */
export async function shareAuthHeaders(): Promise<Record<string, string>> {
  let token: string | null = null
  try {
    token = await getFreshAccessToken()
  } catch {}
  return token ? { Authorization: `Bearer ${token}` } : {}
}

/**
 * What a refresh answers once the session in storage is no longer the one whose
 * refresh token it sent. localStorage is shared by every tab, so another tab
 * may have signed in, signed out or refreshed while the request was out, and
 * writing this answer over it, or clearing it on a refusal, would replace or
 * end that session in every tab. Nothing is written. If storage now holds the
 * same account, another tab refreshed it or signed it in again, and its access
 * token is the answer; otherwise null, "could not refresh".
 */
function sessionMovedOn(sentRefreshToken: string): string | null {
  const access = getAccessToken()
  const sub = subjectOf(access)
  return sub !== null && sub === subjectOf(sentRefreshToken) ? access : null
}

async function _doRefresh(leaveOnRefusal: boolean): Promise<string | null> {
  const refreshToken = getRefreshToken()
  if (!refreshToken) {
    endSession(leaveOnRefusal)
    return null
  }

  try {
    const response = await fetch(`${API_URL}/auth/refresh`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ refresh_token: refreshToken }),
    })

    if (!response.ok) {
      if (getRefreshToken() !== refreshToken) return sessionMovedOn(refreshToken)
      // Only a refusal ends the session. 401 or 403 is the server saying this
      // refresh token is no longer valid, which is what signing out is for.
      // Anything else -- a 502 from a proxy mid-deploy, a 500, a rate limit --
      // is the server being briefly unavailable, and signing the user out for
      // it loses whatever they had open.
      if (response.status === 401 || response.status === 403) {
        endSession(leaveOnRefusal)
      }
      return null
    }

    const data = await response.json()
    if (getRefreshToken() !== refreshToken) return sessionMovedOn(refreshToken)
    const newAccessToken: string = data.access_token
    const newRefreshToken: string = data.refresh_token ?? refreshToken

    setTokens(newAccessToken, newRefreshToken)
    return newAccessToken
  } catch {
    // `fetch` rejects only on a transport failure; an HTTP error status
    // resolves and is handled above. So reaching here means the request never
    // got an answer: no network, DNS, a dropped connection. Clearing tokens
    // here signed people out for going through a tunnel, and took the page
    // they were on with it, since `clearTokens` navigates to /login.
    //
    // Callers treat null as "could not refresh" and let the original failure
    // surface, so keeping the tokens costs nothing and the session survives
    // until the network comes back.
    return null
  }
}
