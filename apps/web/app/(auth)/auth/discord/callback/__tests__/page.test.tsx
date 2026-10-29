import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'

const replace = vi.fn()
vi.mock('next/navigation', () => ({ useRouter: () => ({ push: vi.fn(), replace }) }))

const post = vi.fn()
vi.mock('@/lib/api', () => ({
  api: { post: (...args: unknown[]) => post(...args), get: vi.fn() },
  ApiError: class ApiError extends Error {
    status: number
    detail: string
    constructor(status: number, detail: string) {
      super(detail)
      this.status = status
      this.detail = detail
    }
  },
}))

const completeSignIn = vi.fn().mockResolvedValue(undefined)
vi.mock('@/stores/auth-store', () => ({
  completeSignIn: (...args: unknown[]) => completeSignIn(...args),
}))

import DiscordCallbackPage from '../page'
import { ApiError } from '@/lib/api'

const tokens = { access_token: 'a.b.c', refresh_token: 'r.e.f', needs_password: false }

describe('Discord sign-in callback page', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  afterEach(() => {
    window.history.replaceState(null, '', '/')
  })

  it('trades the ticket, stores the tokens through the shared sign-in path and goes to redirect_to', async () => {
    window.history.replaceState(null, '', '/auth/discord/callback?ticket=t1')
    const res = { ...tokens, redirect_to: '/projects/abc' }
    post.mockResolvedValue(res)
    render(<DiscordCallbackPage />)

    expect(screen.getByText(/signing you in/i)).toBeInTheDocument()
    await waitFor(() => expect(replace).toHaveBeenCalledWith('/projects/abc'))
    expect(post).toHaveBeenCalledWith('/auth/discord/exchange', { ticket: 't1' })
    expect(completeSignIn).toHaveBeenCalledWith(res)
    expect(completeSignIn.mock.invocationCallOrder[0]).toBeLessThan(replace.mock.invocationCallOrder[0])
  })

  it('sanitizes redirect_to again: an off-site target lands on /', async () => {
    window.history.replaceState(null, '', '/auth/discord/callback?ticket=t2')
    post.mockResolvedValue({ ...tokens, redirect_to: '//evil.com' })
    render(<DiscordCallbackPage />)

    await waitFor(() => expect(replace).toHaveBeenCalledWith('/'))
    expect(replace).not.toHaveBeenCalledWith('//evil.com')
  })

  it('sends a flow error back to the login page', async () => {
    window.history.replaceState(null, '', '/auth/discord/callback?error=discord_missing_role')
    render(<DiscordCallbackPage />)

    await waitFor(() => expect(replace).toHaveBeenCalledWith('/login?error=discord_missing_role'))
    expect(post).not.toHaveBeenCalled()
  })

  it('shows the error with a way back when the ticket is refused', async () => {
    window.history.replaceState(null, '', '/auth/discord/callback?ticket=used')
    post.mockRejectedValue(new ApiError(400, 'Invalid or expired sign-in ticket'))
    render(<DiscordCallbackPage />)

    expect(await screen.findByText('Invalid or expired sign-in ticket')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /back to sign in/i })).toHaveAttribute('href', '/login')
    expect(completeSignIn).not.toHaveBeenCalled()
    expect(replace).not.toHaveBeenCalled()
  })
})
