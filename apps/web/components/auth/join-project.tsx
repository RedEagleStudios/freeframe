'use client'

import { useEffect, useState } from 'react'
import Link from 'next/link'
import { useRouter } from 'next/navigation'
import { Loader2, UserPlus } from 'lucide-react'
import { ApiError } from '@/lib/api'
import { getAccessToken, getRefreshToken } from '@/lib/auth'
import { discordStartUrl, fetchAuthProviders } from '@/lib/discord-auth'
import {
  acceptJoinLink,
  JOIN_LINK_ROLES,
  joinLinkInvalidMessage,
  joinLinkRoleLabel,
  previewJoinLink,
  type JoinLinkPreview,
} from '@/lib/project-join-links'
import { DiscordSignInButton } from '@/components/auth/discord-sign-in-button'
import { Button } from '@/components/ui/button'

type Preview =
  | { state: 'loading' }
  | { state: 'valid'; link: JoinLinkPreview }
  | { state: 'invalid'; message: string }
  | { state: 'error' }

/**
 * A project join link: names the project and the role it gives, then joins.
 * Signed in, "Join project" redeems it; signed out, "Continue with Discord"
 * carries the token through sign-in, which redeems it and opens the project.
 * Links are single-use: the first person to join spends it.
 */
export function JoinProject({ token }: { token: string }) {
  const router = useRouter()
  const [preview, setPreview] = useState<Preview>({ state: 'loading' })
  // Read once on the client. The server renders the loading state, which does
  // not depend on it, so the first client render matches.
  const [signedIn] = useState(() => typeof window !== 'undefined' && !!(getAccessToken() || getRefreshToken()))
  const [discord, setDiscord] = useState<boolean | null>(null)
  const [joining, setJoining] = useState(false)
  const [joinError, setJoinError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    previewJoinLink(token)
      .then((link) => {
        if (cancelled) return
        setPreview(link.valid ? { state: 'valid', link } : { state: 'invalid', message: joinLinkInvalidMessage(link.reason) })
      })
      .catch(() => {
        if (!cancelled) setPreview({ state: 'error' })
      })
    if (!signedIn) {
      fetchAuthProviders()
        .then((p) => {
          if (!cancelled) setDiscord(p.discord)
        })
        .catch(() => {
          if (!cancelled) setDiscord(false)
        })
    }
    return () => {
      cancelled = true
    }
  }, [token, signedIn])

  async function join() {
    setJoining(true)
    setJoinError(null)
    try {
      const res = await acceptJoinLink(token)
      router.replace(`/projects/${res.project_id}`)
    } catch (err) {
      setJoinError(
        err instanceof ApiError && (err.status === 404 || err.status === 410)
          ? joinLinkInvalidMessage(err.detail)
          : err instanceof Error && err.message
            ? err.message
            : 'Could not join the project. Please try again.',
      )
      setJoining(false)
    }
  }

  const loginHref = `/login?from=${encodeURIComponent(`/join/${token}`)}`

  if (preview.state === 'loading') {
    return (
      <div className="flex justify-center py-6" aria-label="Loading invite">
        <Loader2 className="h-5 w-5 animate-spin text-text-tertiary" />
      </div>
    )
  }

  if (preview.state !== 'valid') {
    return (
      <div className="animate-slide-up flex flex-col gap-4">
        <h1 className="text-xl font-semibold text-text-primary">Invite link not usable</h1>
        <p role="alert" className="rounded-md border border-status-error/30 bg-status-error/10 px-3 py-2.5 text-sm text-status-error">
          {preview.state === 'invalid' ? preview.message : 'This invite link could not be checked. Please try again.'}
        </p>
        <Link
          href={signedIn ? '/' : '/login'}
          className="text-center text-sm text-text-tertiary transition-colors hover:text-text-secondary"
        >
          {signedIn ? 'Go to your projects' : 'Go to sign in'}
        </Link>
      </div>
    )
  }

  const { link } = preview
  const role = JOIN_LINK_ROLES.find((r) => r.value === link.role)

  return (
    <div className="animate-slide-up flex flex-col gap-4">
      <div className="space-y-1">
        <p className="text-sm text-text-tertiary">You&apos;re invited to join</p>
        <h1 className="break-words text-xl font-semibold text-text-primary">{link.project_name}</h1>
        <p className="text-sm text-text-secondary">
          as <span className="font-medium text-text-primary">{joinLinkRoleLabel(link.role)}</span>
          {role ? ` · ${role.description}` : ''}
        </p>
      </div>

      {signedIn ? (
        <>
          <Button type="button" size="lg" className="w-full" onClick={() => void join()} loading={joining}>
            <UserPlus className="h-4 w-4" />
            Join project
          </Button>
          {joinError && (
            <p role="alert" className="text-sm text-status-error">
              {joinError}
            </p>
          )}
        </>
      ) : discord === null ? (
        <div className="flex justify-center py-2" aria-label="Loading sign-in options">
          <Loader2 className="h-4 w-4 animate-spin text-text-tertiary" />
        </div>
      ) : discord ? (
        <>
          <DiscordSignInButton href={discordStartUrl(`/join/${token}`, token)} divider={false} />
          <Link
            href={loginHref}
            className="text-center text-sm text-text-tertiary transition-colors hover:text-text-secondary"
          >
            Already have an account? Sign in another way
          </Link>
        </>
      ) : (
        <>
          <p className="text-sm text-text-secondary">
            Sign in with Discord is not set up on this instance, so this link can only be used with an existing
            account. Sign in, and you&apos;ll come back here to join.
          </p>
          <Button asChild size="lg" className="w-full">
            <Link href={loginHref}>Sign in with a password</Link>
          </Button>
        </>
      )}
    </div>
  )
}
