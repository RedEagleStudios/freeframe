'use client'

import { Suspense, useEffect, useState } from 'react'
import { useSearchParams } from 'next/navigation'
import { Bot, Check, ShieldAlert } from 'lucide-react'
import { api, ApiError } from '@/lib/api'
import { useAuthStore } from '@/stores/auth-store'
import { Button } from '@/components/ui/button'
import { Switch } from '@/components/ui/switch'
import type { OAuthConsentRedirect, OAuthConsentRequest } from '@/types'

const SCOPE_WRITE = 'freeframe:write'

/** Where the browser goes after the decision, shown so a user can spot a lookalike app. */
function returnTarget(redirectUri: string): string {
  try {
    const url = new URL(redirectUri)
    return url.host ? `${url.protocol}//${url.host}` : url.protocol
  } catch {
    return redirectUri
  }
}

const MISSING_REQUEST = 'This page needs a connection request. Start the connection from your AI app.'

export default function OAuthConsentPage() {
  // useSearchParams needs a Suspense boundary to prerender.
  return (
    <Suspense fallback={<p className="text-sm text-text-secondary text-center">Loading…</p>}>
      <OAuthConsent />
    </Suspense>
  )
}

/**
 * An AI app (an MCP client) asking to act as the signed-in user. The API's
 * /authorize sends the browser here with the parked request's id; the proxy
 * has already made sure someone is signed in.
 */
function OAuthConsent() {
  const requestId = useSearchParams().get('request')
  const user = useAuthStore((s) => s.user)
  const [request, setRequest] = useState<OAuthConsentRequest | null>(null)
  const [allowWrite, setAllowWrite] = useState(true)
  const [busy, setBusy] = useState<'approve' | 'deny' | null>(null)
  const [done, setDone] = useState<'approved' | 'denied' | null>(null)
  const [error, setError] = useState(requestId ? '' : MISSING_REQUEST)

  useEffect(() => {
    if (!requestId) return
    useAuthStore.getState().fetchUser()
    api
      .get<OAuthConsentRequest>(`/oauth/requests/${encodeURIComponent(requestId)}`)
      .then(setRequest)
      .catch((err) => setError(err instanceof ApiError ? err.detail : 'Could not load the connection request.'))
  }, [requestId])

  async function decide(approve: boolean) {
    if (!requestId) return
    setBusy(approve ? 'approve' : 'deny')
    setError('')
    try {
      const path = `/oauth/requests/${encodeURIComponent(requestId)}/${approve ? 'approve' : 'deny'}`
      const res = await api.post<OAuthConsentRedirect>(path, approve ? { allow_write: allowWrite } : undefined)
      setDone(approve ? 'approved' : 'denied')
      window.location.assign(res.redirect_url)
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'Something went wrong. Please try again.')
    } finally {
      setBusy(null)
    }
  }

  if (done) {
    return (
      <div className="space-y-2 text-center">
        <h1 className="text-xl font-semibold text-text-primary">
          {done === 'approved' ? 'Connected' : 'Connection declined'}
        </h1>
        <p className="text-sm text-text-secondary">
          You can close this tab and go back to {request?.client_name ?? 'your app'}.
        </p>
      </div>
    )
  }

  if (!request) {
    return error ? (
      <div className="space-y-2 text-center">
        <h1 className="text-xl font-semibold text-text-primary">Can&apos;t connect</h1>
        <p className="text-sm text-text-secondary">{error}</p>
      </div>
    ) : (
      <p className="text-sm text-text-secondary text-center">Loading…</p>
    )
  }

  return (
    <div className="space-y-5">
      <div className="space-y-2 text-center">
        <div className="mx-auto flex h-10 w-10 items-center justify-center rounded-xl bg-accent-muted">
          <Bot className="h-5 w-5 text-text-primary" />
        </div>
        <h1 className="text-xl font-semibold text-text-primary">
          <span className="break-words">{request.client_name}</span> wants to use your account
        </h1>
        {user && <p className="text-xs text-text-tertiary">Signed in as {user.email}</p>}
      </div>

      <ul className="space-y-3">
        {request.scopes.map((scope) => (
          <li key={scope.name} className="flex items-start gap-2.5 text-sm text-text-secondary">
            {scope.name === SCOPE_WRITE ? (
              <>
                <Switch
                  id="allow-write"
                  checked={allowWrite}
                  onCheckedChange={setAllowWrite}
                  aria-label="Allow changes"
                  className="mt-0.5 shrink-0 scale-75 origin-left"
                />
                <label htmlFor="allow-write" className="cursor-pointer">
                  {scope.description}
                  {!allowWrite && <span className="block text-2xs text-text-tertiary">Off: a read-only connection</span>}
                </label>
              </>
            ) : (
              <>
                <Check className="h-4 w-4 mt-0.5 shrink-0 text-status-success" />
                <span>{scope.description}</span>
              </>
            )}
          </li>
        ))}
      </ul>

      <div className="flex gap-2 rounded-lg border border-border bg-bg-secondary/50 p-3 text-2xs text-text-tertiary">
        <ShieldAlert className="h-4 w-4 shrink-0" />
        <p>
          Only continue if you started this connection yourself. The app acts as you and sees what you can see.
          After you decide you go back to <span className="font-mono text-text-secondary break-all">{returnTarget(request.redirect_uri)}</span>.
          Disconnect it any time under AI access in your settings.
        </p>
      </div>

      {error && (
        <p className="rounded-lg border border-status-error/30 bg-status-error/10 p-2.5 text-xs text-status-error">{error}</p>
      )}

      <div className="flex gap-2">
        <Button
          variant="secondary"
          className="flex-1"
          onClick={() => decide(false)}
          loading={busy === 'deny'}
          disabled={busy !== null}
        >
          Cancel
        </Button>
        <Button
          variant="primary"
          className="flex-1"
          onClick={() => decide(true)}
          loading={busy === 'approve'}
          disabled={busy !== null}
        >
          Allow
        </Button>
      </div>
    </div>
  )
}
