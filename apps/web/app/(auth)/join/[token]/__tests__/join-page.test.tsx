import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'

const { api, nav, session } = vi.hoisted(() => ({
  api: { get: vi.fn(), post: vi.fn() },
  nav: { replace: vi.fn(), push: vi.fn() },
  session: { access: null as string | null, refresh: null as string | null },
}))
vi.mock('next/navigation', () => ({ useRouter: () => nav }))
vi.mock('@/lib/api', async (orig) => ({ ...(await orig<typeof import('@/lib/api')>()), api }))
vi.mock('@/lib/auth', () => ({
  getAccessToken: () => session.access,
  getRefreshToken: () => session.refresh,
  setTokens: vi.fn(),
}))

import { ApiError } from '@/lib/api'
import { JoinProject } from '@/components/auth/join-project'

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'
const TOKEN = 'abc_DEF-123'

let preview: unknown
let providers: unknown

beforeEach(() => {
  session.access = null
  session.refresh = null
  preview = { valid: true, project_name: 'Launch film', role: 'reviewer', reason: null }
  providers = { discord: true, email_code: false }
  nav.replace.mockReset()
  api.get.mockReset().mockImplementation(async (url: string) => {
    if (url === `/project-join-links/${TOKEN}`) return preview
    if (url === '/auth/providers') return providers
    throw new Error(`unexpected GET ${url}`)
  })
  api.post.mockReset()
})

describe('a valid link', () => {
  it('signed out: names the project and role, and Discord carries the join token', async () => {
    render(<JoinProject token={TOKEN} />)
    expect(await screen.findByRole('heading', { name: 'Launch film' })).toBeInTheDocument()
    expect(screen.getByText('Reviewer')).toBeInTheDocument()
    const discord = await screen.findByRole('link', { name: /continue with discord/i })
    expect(discord).toHaveAttribute(
      'href',
      `${API_URL}/auth/discord/start?from=${encodeURIComponent(`/join/${TOKEN}`)}&join=${TOKEN}`,
    )
    expect(screen.queryByRole('button', { name: 'Join project' })).toBeNull()
  })

  it('signed out without Discord: explains and links to password sign-in back here', async () => {
    providers = { discord: false, email_code: false }
    render(<JoinProject token={TOKEN} />)
    expect(await screen.findByText(/Sign in with Discord is not set up/)).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: /continue with discord/i })).toBeNull()
    expect(screen.getByRole('link', { name: 'Sign in with a password' })).toHaveAttribute(
      'href',
      `/login?from=${encodeURIComponent(`/join/${TOKEN}`)}`,
    )
  })

  it('signed in: Join project accepts, then opens the project', async () => {
    session.access = 'a-token'
    api.post.mockResolvedValue({ project_id: 'p42', role: 'reviewer' })
    render(<JoinProject token={TOKEN} />)
    fireEvent.click(await screen.findByRole('button', { name: 'Join project' }))
    await waitFor(() => expect(api.post).toHaveBeenCalledWith(`/project-join-links/${TOKEN}/accept`))
    await waitFor(() => expect(nav.replace).toHaveBeenCalledWith('/projects/p42'))
    expect(screen.queryByRole('link', { name: /continue with discord/i })).toBeNull()
  })

  it('signed in: a link spent by someone else meanwhile says so', async () => {
    session.refresh = 'r-token'
    api.post.mockRejectedValue(new ApiError(410, 'used_up'))
    render(<JoinProject token={TOKEN} />)
    fireEvent.click(await screen.findByRole('button', { name: 'Join project' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'This invite link has already been used. Ask the project owner for a new one.',
    )
    expect(nav.replace).not.toHaveBeenCalled()
  })
})

describe('an unusable link', () => {
  it.each([
    ['used_up', 'This invite link has already been used. Ask the project owner for a new one.'],
    ['expired', 'This invite link has expired. Ask the project owner for a new one.'],
    ['revoked', 'This invite link was revoked. Ask the project owner for a new one.'],
    ['not_found', 'This invite link is not valid.'],
  ])('%s: shows its message and no way to join', async (reason, message) => {
    preview = { valid: false, project_name: null, role: null, reason }
    render(<JoinProject token={TOKEN} />)
    expect(await screen.findByRole('alert')).toHaveTextContent(message)
    expect(screen.queryByRole('link', { name: /continue with discord/i })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Join project' })).toBeNull()
  })
})
