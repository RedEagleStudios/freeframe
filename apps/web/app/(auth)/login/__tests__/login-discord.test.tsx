import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest'
import { act, render, screen, waitFor } from '@testing-library/react'

const replace = vi.fn()
vi.mock('next/navigation', () => ({ useRouter: () => ({ push: vi.fn(), replace }) }))

const get = vi.fn()
vi.mock('@/lib/api', () => ({
  api: { get: (...args: unknown[]) => get(...args), post: vi.fn() },
  ApiError: class ApiError extends Error {},
}))
vi.mock('@/lib/auth', () => ({ getAccessToken: () => null, setTokens: vi.fn() }))

import LoginPage from '../page'
import { useBrandingStore } from '@/stores/branding-store'

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'

function answer(providers: { discord: boolean; email_code?: boolean } | Error) {
  get.mockImplementation((path: string) => {
    if (path === '/setup/status') return Promise.resolve({ needs_setup: false })
    if (path === '/auth/providers') {
      return providers instanceof Error ? Promise.reject(providers) : Promise.resolve(providers)
    }
    return Promise.reject(new Error(`unexpected ${path}`))
  })
}

const discordLink = () => screen.queryByRole('link', { name: /continue with discord/i })

describe('Login page: Sign in with Discord', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    act(() => useBrandingStore.setState({ loaded: true, defaultLoginMode: 'magic_code' }))
  })

  afterEach(() => {
    window.history.replaceState(null, '', '/')
  })

  it('links to the API start URL carrying the sanitized `from` when Discord is enabled', async () => {
    window.history.replaceState(null, '', '/login?from=/projects/abc?tab=comments')
    answer({ discord: true })
    render(<LoginPage />)

    await waitFor(() => expect(discordLink()).toBeInTheDocument())
    expect(discordLink()).toHaveAttribute(
      'href',
      `${API_URL}/auth/discord/start?from=${encodeURIComponent('/projects/abc?tab=comments')}`,
    )
  })

  it('never carries an off-site `from` to the start URL', async () => {
    window.history.replaceState(null, '', '/login?from=//evil.com')
    answer({ discord: true })
    render(<LoginPage />)

    await waitFor(() => expect(discordLink()).toBeInTheDocument())
    expect(discordLink()).toHaveAttribute('href', `${API_URL}/auth/discord/start?from=%2Fprojects`)
  })

  it('shows no button when Discord is disabled', async () => {
    window.history.replaceState(null, '', '/login')
    answer({ discord: false })
    render(<LoginPage />)

    await waitFor(() => expect(get).toHaveBeenCalledWith('/auth/providers'))
    expect(discordLink()).not.toBeInTheDocument()
  })

  it('shows no button when the providers request fails', async () => {
    window.history.replaceState(null, '', '/login')
    answer(new Error('down'))
    render(<LoginPage />)

    await waitFor(() => expect(get).toHaveBeenCalledWith('/auth/providers'))
    expect(discordLink()).not.toBeInTheDocument()
  })

  it('renders a Discord `?error=` code as the form error', async () => {
    window.history.replaceState(null, '', '/login?error=discord_no_account')
    answer({ discord: true })
    render(<LoginPage />)

    expect(
      await screen.findByText('No FreeFrame account uses this Discord email. Ask an admin to invite you.'),
    ).toBeInTheDocument()
  })

  it('ignores an unknown `?error=` value', async () => {
    window.history.replaceState(null, '', '/login?error=<script>')
    answer({ discord: false })
    render(<LoginPage />)

    await waitFor(() => expect(get).toHaveBeenCalledWith('/auth/providers'))
    expect(screen.queryByText('<script>')).not.toBeInTheDocument()
  })
})

describe('Login page: sign-in codes switched off', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    act(() => useBrandingStore.setState({ loaded: true, defaultLoginMode: 'magic_code' }))
    window.history.replaceState(null, '', '/login')
  })

  it('opens on password sign-in with no way to request a code, even when codes are the default mode', async () => {
    answer({ discord: true, email_code: false })
    render(<LoginPage />)

    await waitFor(() => expect(screen.getByPlaceholderText('Your password')).toBeInTheDocument())
    expect(screen.queryByRole('button', { name: /magic code/i })).not.toBeInTheDocument()
    expect(discordLink()).toBeInTheDocument()
  })

  it('keeps the code screen when the server offers codes', async () => {
    answer({ discord: false, email_code: true })
    render(<LoginPage />)

    await waitFor(() => expect(get).toHaveBeenCalledWith('/auth/providers'))
    expect(screen.getByRole('button', { name: /send magic code/i })).toBeInTheDocument()
  })
})
