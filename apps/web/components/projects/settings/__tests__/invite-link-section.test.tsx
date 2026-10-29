import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { SWRConfig } from 'swr'

const { api, copy } = vi.hoisted(() => ({
  api: { get: vi.fn(), patch: vi.fn(), put: vi.fn(), post: vi.fn(), delete: vi.fn() },
  copy: vi.fn(),
}))
vi.mock('@/lib/api', async (orig) => ({ ...(await orig<typeof import('@/lib/api')>()), api }))
vi.mock('@/lib/utils', async (orig) => ({ ...(await orig<typeof import('@/lib/utils')>()), copyToClipboard: copy }))

import { InviteLinkSection } from '../invite-link-section'

// Single-use invite links on the People tab: create one with a role and an
// expiry, see its URL once, copy it, and revoke links that are still unused.

const BASE = {
  project_id: 'p1', created_by: 'u1', created_by_name: 'Olivia Owner', created_at: '2026-10-20T10:00:00Z',
  revoked_at: null,
}

let links: Record<string, unknown>[]

function renderSection() {
  return render(
    <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0, shouldRetryOnError: false }}>
      <InviteLinkSection projectId="p1" />
    </SWRConfig>,
  )
}

beforeEach(() => {
  links = [
    { ...BASE, id: 'l1', role: 'editor', status: 'active', expires_at: '2026-11-01T10:00:00Z', used_at: null, used_by: null, used_by_name: null },
    { ...BASE, id: 'l2', role: 'viewer', status: 'used', expires_at: null, used_at: '2026-10-21T09:30:00Z', used_by: 'u9', used_by_name: 'Guest Gail' },
  ]
  api.get.mockReset().mockImplementation(async (url: string) => {
    if (url === '/projects/p1/join-links') return links
    throw new Error(`unexpected GET ${url}`)
  })
  api.post.mockReset()
  api.delete.mockReset().mockResolvedValue(undefined)
  copy.mockReset().mockResolvedValue(true)
})

describe('the invite link list', () => {
  it('shows who used a link and when, and offers Revoke only on an unused one', async () => {
    renderSection()
    const list = await screen.findByRole('list', { name: 'Invite links' })
    const [active, used] = within(list).getAllByRole('listitem')
    expect(active).toHaveTextContent('Editor')
    expect(active).toHaveTextContent(/Unused · expires/)
    expect(within(active).getByRole('button', { name: /Revoke/ })).toBeInTheDocument()
    expect(used).toHaveTextContent(/Used by Guest Gail · Oct 21, 2026/)
    expect(within(used).queryByRole('button', { name: /Revoke/ })).toBeNull()
  })

  it('revoke calls the API and reloads the list', async () => {
    renderSection()
    const list = await screen.findByRole('list', { name: 'Invite links' })
    links = [{ ...links[0], status: 'revoked', revoked_at: '2026-10-22T00:00:00Z' }, links[1]]
    fireEvent.click(within(list).getByRole('button', { name: /Revoke/ }))
    await waitFor(() => expect(api.delete).toHaveBeenCalledWith('/projects/p1/join-links/l1'))
    await waitFor(() => expect(screen.getByText(/^Revoked/)).toBeInTheDocument())
  })
})

describe('creating a link', () => {
  it('sends the role and expiry, shows the URL once with a warning, and copies it', async () => {
    api.post.mockResolvedValue({
      ...BASE, id: 'l3', role: 'editor', status: 'active', expires_at: null, used_at: null, used_by: null,
      used_by_name: null, token: 'secret-token', url: 'https://ff.example.com/join/secret-token',
    })
    renderSection()
    await screen.findByRole('list', { name: 'Invite links' })
    // No "max uses": every link is single-use.
    expect(screen.queryByLabelText(/max uses/i)).toBeNull()
    expect(screen.getByLabelText('Role')).toHaveValue('reviewer')
    expect(screen.getByLabelText('Expires after')).toHaveValue('7')

    fireEvent.change(screen.getByLabelText('Role'), { target: { value: 'editor' } })
    fireEvent.change(screen.getByLabelText('Expires after'), { target: { value: 'never' } })
    fireEvent.click(screen.getByRole('button', { name: 'Create link' }))

    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith('/projects/p1/join-links', { role: 'editor', expires_in_days: null }),
    )
    expect(await screen.findByLabelText('New invite link')).toHaveValue('https://ff.example.com/join/secret-token')
    expect(screen.getByText(/Anyone with this link can join as Editor\. It's only shown once\./)).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Copy' }))
    await waitFor(() => expect(copy).toHaveBeenCalledWith('https://ff.example.com/join/secret-token'))
    expect(await screen.findByRole('button', { name: 'Copied' })).toBeInTheDocument()
  })

  it('sends a day count for a timed expiry', async () => {
    api.post.mockResolvedValue({ ...BASE, id: 'l4', role: 'reviewer', status: 'active', expires_at: null, used_at: null, used_by: null, used_by_name: null, token: 't', url: 'u' })
    renderSection()
    await screen.findByRole('list', { name: 'Invite links' })
    fireEvent.change(screen.getByLabelText('Expires after'), { target: { value: '30' } })
    fireEvent.click(screen.getByRole('button', { name: 'Create link' }))
    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith('/projects/p1/join-links', { role: 'reviewer', expires_in_days: 30 }),
    )
  })

  it('the URL is gone after a reload: the list never carries tokens', async () => {
    api.post.mockResolvedValue({ ...BASE, id: 'l5', role: 'viewer', status: 'active', expires_at: null, used_at: null, used_by: null, used_by_name: null, token: 'tok-5', url: 'https://x/join/tok-5' })
    renderSection()
    await screen.findByRole('list', { name: 'Invite links' })
    fireEvent.click(screen.getByRole('button', { name: 'Create link' }))
    expect(await screen.findByLabelText('New invite link')).toBeInTheDocument()
    const view = renderSection()
    await waitFor(() => expect(within(view.container).getByRole('list', { name: 'Invite links' })).toBeInTheDocument())
    expect(within(view.container).queryByLabelText('New invite link')).toBeNull()
    expect(view.container.textContent).not.toContain('tok-5')
  })

  it('shows why a create failed', async () => {
    api.post.mockRejectedValue(new Error('Project owner access required'))
    renderSection()
    await screen.findByRole('list', { name: 'Invite links' })
    fireEvent.click(screen.getByRole('button', { name: 'Create link' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Project owner access required')
  })
})
