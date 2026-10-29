'use client'

import * as React from 'react'
import useSWR from 'swr'
import { AlertTriangle, Check, ChevronDown, Copy, Link2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { copyToClipboard } from '@/lib/utils'
import {
  createJoinLink,
  JOIN_LINK_EXPIRIES,
  JOIN_LINK_ROLES,
  joinLinkRoleLabel,
  listJoinLinks,
  revokeJoinLink,
  type CreatedProjectJoinLink,
  type JoinLinkRole,
  type ProjectJoinLink,
} from '@/lib/project-join-links'

const SELECT_CLASS =
  'w-full appearance-none cursor-pointer rounded-md border border-border bg-bg-tertiary py-1.5 pl-3 pr-8 text-sm text-text-primary transition-colors hover:border-border-focus focus:outline-none focus:border-border-focus focus:ring-1 focus:ring-border-focus disabled:cursor-not-allowed disabled:opacity-50'

function errorText(err: unknown, fallback: string): string {
  return err instanceof Error && err.message ? err.message : fallback
}

function formatWhen(iso: string | null | undefined): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '—'
  return d.toLocaleString('en-US', { month: 'short', day: 'numeric', year: 'numeric', hour: 'numeric', minute: '2-digit' })
}

function linkState(link: ProjectJoinLink): string {
  switch (link.status) {
    case 'used':
      return `Used by ${link.used_by_name ?? 'a deleted user'} · ${formatWhen(link.used_at)}`
    case 'expired':
      return `Expired ${formatWhen(link.expires_at)}`
    case 'revoked':
      return `Revoked ${formatWhen(link.revoked_at)}`
    default:
      return link.expires_at ? `Unused · expires ${formatWhen(link.expires_at)}` : 'Unused · never expires'
  }
}

/**
 * "Invite link" on the People tab: single-use links that let one person join
 * this project with a chosen role, signing in with Discord even when they are
 * not in the team's Discord server. The URL is shown once, at creation.
 */
export function InviteLinkSection({ projectId }: { projectId: string }) {
  const key = `/projects/${projectId}/join-links`
  const { data: links, error: listError, mutate } = useSWR<ProjectJoinLink[]>(key, () => listJoinLinks(projectId))

  const [role, setRole] = React.useState<JoinLinkRole>('reviewer')
  const [expires, setExpires] = React.useState<string>('7')
  const [creating, setCreating] = React.useState(false)
  const [created, setCreated] = React.useState<CreatedProjectJoinLink | null>(null)
  const [copied, setCopied] = React.useState(false)
  const [error, setError] = React.useState<string | null>(null)
  const [revoking, setRevoking] = React.useState<string | null>(null)

  async function create() {
    setCreating(true)
    setError(null)
    setCopied(false)
    try {
      const link = await createJoinLink(projectId, {
        role,
        expires_in_days: expires === 'never' ? null : (Number(expires) as 1 | 7 | 30),
      })
      setCreated(link)
      await mutate()
    } catch (err) {
      setError(errorText(err, 'Could not create the link'))
    } finally {
      setCreating(false)
    }
  }

  async function copy() {
    if (!created) return
    const ok = await copyToClipboard(created.url)
    setCopied(ok)
    if (!ok) setError('Could not copy. Select the link and copy it by hand.')
  }

  async function revoke(link: ProjectJoinLink) {
    setRevoking(link.id)
    setError(null)
    try {
      await revokeJoinLink(projectId, link.id)
      if (created?.id === link.id) setCreated(null)
      await mutate()
    } catch (err) {
      setError(errorText(err, 'Could not revoke the link'))
    } finally {
      setRevoking(null)
    }
  }

  return (
    <section aria-labelledby="invite-link-heading" className="rounded-xl border border-border bg-bg-secondary p-4 space-y-4">
      <div className="space-y-1">
        <h2 id="invite-link-heading" className="flex items-center gap-1.5 text-sm font-semibold text-text-primary">
          <Link2 className="h-4 w-4" />
          Invite link
        </h2>
        <p className="text-xs text-text-tertiary">
          A link one person can use to join this project, signing in with Discord, even if they are not in your
          Discord server. They see only this project. Each link works once: make a new one for each person.
        </p>
      </div>

      <div className="grid gap-3 sm:grid-cols-[1fr_1fr_auto] sm:items-end">
        <div className="flex flex-col gap-1.5">
          <label htmlFor="join-link-role" className="text-xs font-medium text-text-secondary">
            Role
          </label>
          <div className="relative">
            <select
              id="join-link-role"
              value={role}
              onChange={(e) => setRole(e.target.value as JoinLinkRole)}
              className={SELECT_CLASS}
            >
              {JOIN_LINK_ROLES.map((r) => (
                <option key={r.value} value={r.value}>
                  {r.label} — {r.description}
                </option>
              ))}
            </select>
            <ChevronDown className="pointer-events-none absolute right-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-text-tertiary" />
          </div>
        </div>
        <div className="flex flex-col gap-1.5">
          <label htmlFor="join-link-expiry" className="text-xs font-medium text-text-secondary">
            Expires after
          </label>
          <div className="relative">
            <select
              id="join-link-expiry"
              value={expires}
              onChange={(e) => setExpires(e.target.value)}
              className={SELECT_CLASS}
            >
              {JOIN_LINK_EXPIRIES.map((x) => (
                <option key={x.label} value={x.value === null ? 'never' : String(x.value)}>
                  {x.label}
                </option>
              ))}
            </select>
            <ChevronDown className="pointer-events-none absolute right-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-text-tertiary" />
          </div>
        </div>
        <Button type="button" size="sm" onClick={() => void create()} loading={creating}>
          Create link
        </Button>
      </div>

      {created && (
        <div className="space-y-2 rounded-lg border border-accent/30 bg-accent/5 p-3">
          <div className="flex gap-2">
            <input
              readOnly
              value={created.url}
              aria-label="New invite link"
              onFocus={(e) => e.currentTarget.select()}
              className="min-w-0 flex-1 rounded-md border border-border bg-bg-tertiary px-3 py-1.5 font-mono text-xs text-text-primary outline-none focus:border-border-focus"
            />
            <Button type="button" size="sm" variant="secondary" onClick={() => void copy()}>
              {copied ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
              {copied ? 'Copied' : 'Copy'}
            </Button>
          </div>
          <p className="flex items-start gap-1.5 text-xs text-status-warning">
            <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
            Anyone with this link can join as {joinLinkRoleLabel(created.role)}. It&apos;s only shown once.
          </p>
        </div>
      )}

      {error && <p role="alert" className="text-xs text-status-error">{error}</p>}

      {listError ? (
        <p role="alert" className="text-xs text-status-error">
          Links could not be loaded: {errorText(listError, 'request failed')}
        </p>
      ) : links && links.length > 0 ? (
        <ul aria-label="Invite links" className="divide-y divide-border rounded-lg border border-border">
          {links.map((link) => (
            <li key={link.id} className="flex items-center gap-3 px-3 py-2">
              <div className="min-w-0 flex-1">
                <p className="text-sm text-text-primary">
                  {joinLinkRoleLabel(link.role)}
                  <span className="ml-1.5 text-xs text-text-tertiary">
                    by {link.created_by_name ?? 'a deleted user'} · {formatWhen(link.created_at)}
                  </span>
                </p>
                <p className="truncate text-xs text-text-tertiary">{linkState(link)}</p>
              </div>
              {link.status === 'active' && (
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  aria-label={`Revoke ${joinLinkRoleLabel(link.role)} link created ${formatWhen(link.created_at)}`}
                  onClick={() => void revoke(link)}
                  loading={revoking === link.id}
                >
                  Revoke
                </Button>
              )}
            </li>
          ))}
        </ul>
      ) : null}
    </section>
  )
}
