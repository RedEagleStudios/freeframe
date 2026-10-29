'use client'

import { useEffect, useRef, useState } from 'react'
import Link from 'next/link'
import { useRouter } from 'next/navigation'
import { Loader2 } from 'lucide-react'
import { ApiError } from '@/lib/api'
import { exchangeDiscordTicket } from '@/lib/discord-auth'
import { safeInternalPath } from '@/lib/safe-redirect'
import { completeSignIn } from '@/stores/auth-store'

/**
 * Where the API sends the browser after Discord: it carries a one-time ticket,
 * never tokens. Trading the ticket in here keeps the tokens out of every URL,
 * browser history entry and access log.
 */
export default function DiscordCallbackPage() {
  const router = useRouter()
  const [error, setError] = useState<string | null>(null)
  // The ticket is single-use: a second exchange (React's dev double-mount)
  // would fail and show an error over a sign-in that worked.
  const started = useRef(false)

  useEffect(() => {
    if (started.current) return
    started.current = true

    const params = new URLSearchParams(window.location.search)
    const flowError = params.get('error')
    if (flowError) {
      router.replace(`/login?error=${encodeURIComponent(flowError)}`)
      return
    }
    const ticket = params.get('ticket')
    if (!ticket) {
      setError('This sign-in link is incomplete.')
      return
    }

    exchangeDiscordTicket(ticket)
      .then(async (res) => {
        await completeSignIn(res)
        // The API sanitizes this too; check again, it is about to be navigated to.
        router.replace(safeInternalPath(res.redirect_to, '/'))
      })
      .catch((err: unknown) => {
        setError(err instanceof ApiError ? err.detail : 'Signing in with Discord failed. Please try again.')
      })
  }, [router])

  if (error) {
    return (
      <div className="animate-slide-up flex flex-col gap-4">
        <h1 className="text-xl font-semibold text-text-primary">Sign-in failed</h1>
        <div className="rounded-md border border-status-error/30 bg-status-error/10 px-3 py-2.5 text-sm text-status-error">
          {error}
        </div>
        <Link
          href="/login"
          className="text-center text-sm text-text-tertiary transition-colors hover:text-text-secondary"
        >
          Back to sign in
        </Link>
      </div>
    )
  }

  return (
    <div className="flex flex-col items-center gap-3 py-6" role="status">
      <Loader2 className="h-5 w-5 animate-spin text-text-tertiary" />
      <p className="text-sm text-text-secondary">Signing you in…</p>
    </div>
  )
}
