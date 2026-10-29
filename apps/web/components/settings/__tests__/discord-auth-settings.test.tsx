import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'

const h = vi.hoisted(() => {
  // A STABLE `data` reference, like SWR's cache: a fresh object per render
  // would re-run the form's sync effect and clobber what was typed.
  const SAVED = {
    enabled: false,
    client_id: '1111',
    client_secret_set: true,
    server_id: null as string | null,
    allowed_role_ids: [] as string[],
    redirect_uri: 'https://ff.example.com/api/auth/discord/callback',
    configured_via: 'database' as const,
  }
  return { SAVED, data: SAVED as typeof SAVED | undefined }
})
vi.mock('swr', () => ({
  default: () => ({ data: h.data, isLoading: false }),
  mutate: vi.fn(),
}))
const put = vi.fn()
vi.mock('@/lib/api', () => ({ api: { put: (...a: unknown[]) => put(...a), get: vi.fn() } }))

import { DiscordAuthSettings } from '../discord-auth-settings'
import { ToastProvider } from '@/components/shared/toast'

function renderCard() {
  return render(
    <ToastProvider>
      <DiscordAuthSettings />
    </ToastProvider>,
  )
}

const save = () => fireEvent.click(screen.getByRole('button', { name: /^save$/i }))
const lastBody = () => put.mock.calls[put.mock.calls.length - 1][1] as Record<string, unknown>

describe('DiscordAuthSettings', () => {
  beforeEach(() => {
    put.mockReset()
    put.mockResolvedValue(h.SAVED)
    h.data = h.SAVED
  })

  it('does not send client_secret when the field is left blank', async () => {
    renderCard()
    expect(screen.getByLabelText(/client secret/i)).toHaveAttribute('placeholder', 'Saved — leave blank to keep')

    fireEvent.click(screen.getByRole('switch', { name: /enable sign in with discord/i }))
    save()

    await waitFor(() => expect(put).toHaveBeenCalledTimes(1))
    expect(put.mock.calls[0][0]).toBe('/admin/auth/discord')
    expect(lastBody()).toEqual({ enabled: true, client_id: '1111', server_id: null, allowed_role_ids: [] })
    expect(lastBody()).not.toHaveProperty('client_secret')
    expect(await screen.findByText(/settings saved/i)).toBeInTheDocument()
  })

  it('sends a typed secret, the server ID and the parsed role list', async () => {
    renderCard()
    fireEvent.change(screen.getByLabelText(/client secret/i), { target: { value: ' s3cret ' } })
    fireEvent.change(screen.getByLabelText(/server id/i), { target: { value: '999' } })
    fireEvent.change(screen.getByLabelText(/allowed role ids/i), { target: { value: '10, 20\n30\n\n10' } })
    save()

    await waitFor(() => expect(put).toHaveBeenCalledTimes(1))
    expect(lastBody()).toEqual({
      enabled: false,
      client_id: '1111',
      client_secret: 's3cret',
      server_id: '999',
      allowed_role_ids: ['10', '20', '30'],
    })
  })

  it('refuses roles without a server ID instead of sending them', async () => {
    renderCard()
    fireEvent.change(screen.getByLabelText(/allowed role ids/i), { target: { value: '10' } })
    save()

    expect(await screen.findByText(/set a server id/i)).toBeInTheDocument()
    expect(put).not.toHaveBeenCalled()
  })

  it('"Remove secret" sends only an empty client_secret', async () => {
    renderCard()
    fireEvent.click(screen.getByRole('button', { name: /remove secret/i }))

    await waitFor(() => expect(put).toHaveBeenCalledTimes(1))
    expect(lastBody()).toEqual({ client_secret: '' })
  })

  it('shows a failed save as an error toast', async () => {
    put.mockRejectedValue(new Error('Client ID and secret are required to enable'))
    renderCard()
    save()

    expect(await screen.findByText('Client ID and secret are required to enable')).toBeInTheDocument()
  })

  it('notes when the values come from environment variables', () => {
    h.data = { ...h.SAVED, configured_via: 'environment' as never }
    renderCard()
    expect(screen.getByText(/environment variables/i)).toBeInTheDocument()
  })
})
