import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react'
import type { Project } from '@/types'

// A superadmin can switch the Projects page to All projects (?scope=all), see
// projects they aren't in, and join one as owner. Everyone else keeps the page
// as it was.

const { state, nav, api, toast } = vi.hoisted(() => ({
  state: {
    user: { id: 'admin', is_superadmin: true } as { id: string; is_superadmin: boolean },
    search: '',
    projects: [] as Project[],
  },
  nav: { push: vi.fn(), replace: vi.fn() },
  api: { get: vi.fn(), post: vi.fn(), delete: vi.fn() },
  toast: { success: vi.fn(), error: vi.fn() },
}))

vi.mock('next/navigation', () => ({
  useRouter: () => nav,
  useSearchParams: () => new URLSearchParams(state.search),
}))
vi.mock('swr', () => ({
  default: (_key: string, fetcher: () => unknown) => {
    fetcher()
    return { data: state.projects, isLoading: false, mutate: vi.fn() }
  },
}))
vi.mock('@/lib/api', () => ({ api }))
vi.mock('@/components/shared/toast', () => ({ useToast: () => toast }))
vi.mock('@/hooks/use-page-title', () => ({ usePageTitle: () => undefined }))
vi.mock('@/hooks/use-projects-view-ready', () => ({ useProjectsViewReady: () => true }))
vi.mock('@/stores/auth-store', () => ({
  useAuthStore: (selector?: (s: unknown) => unknown) => {
    const s = { user: state.user }
    return typeof selector === 'function' ? selector(s) : s
  },
}))
vi.mock('@/stores/projects-view-store', () => ({
  useProjectsViewStore: () => ({
    layout: 'grid',
    cardSize: 'M',
    showCardInfo: true,
    sortKey: 'name',
    sortDirection: 'asc',
  }),
}))
vi.mock('@/components/projects/projects-appearance-popover', () => ({ ProjectsAppearancePopover: () => null }))
vi.mock('@/components/projects/projects-sort-popover', () => ({ ProjectsSortPopover: () => null }))

import ProjectsPage from '../page'

function project(id: string, name: string, extra: Partial<Project> = {}): Project {
  return {
    id, name,
    description: null,
    created_by: 'alice',
    project_type: 'personal',
    created_at: '2026-01-01T00:00:00Z',
    deleted_at: null,
    ...extra,
  }
}

const memberProject = project('p-member', 'Shared Reel', { role: 'editor' })
const orphanProject = project('p-orphan', 'Orphan Cut', {
  role: null,
  owners: [{ id: 'alice', name: 'Alice', email: 'alice@x.test' }],
})

beforeEach(() => {
  vi.clearAllMocks()
  api.get.mockResolvedValue([])
  api.post.mockResolvedValue({ ...orphanProject, role: 'owner' })
  state.user = { id: 'admin', is_superadmin: true }
  state.search = ''
  state.projects = [memberProject]
})

describe('project scope switch', () => {
  it('is hidden from non-admins, who only ever list their own projects', () => {
    state.user = { id: 'u1', is_superadmin: false }
    state.search = 'scope=all'
    render(<ProjectsPage />)
    expect(screen.queryByRole('group', { name: 'Which projects' })).toBeNull()
    expect(api.get).toHaveBeenCalledWith('/projects')
    expect(api.get).not.toHaveBeenCalledWith('/projects?scope=all')
  })

  it('puts All projects in the URL for an admin', () => {
    render(<ProjectsPage />)
    const group = screen.getByRole('group', { name: 'Which projects' })
    fireEvent.click(within(group).getByRole('button', { name: 'All projects' }))
    expect(nav.replace).toHaveBeenCalledWith('/projects?scope=all', { scroll: false })
  })

  it('requests every project when the URL says scope=all', () => {
    state.search = 'scope=all'
    render(<ProjectsPage />)
    expect(api.get).toHaveBeenCalledWith('/projects?scope=all')
    const group = screen.getByRole('group', { name: 'Which projects' })
    expect(within(group).getByRole('button', { name: 'All projects' }).getAttribute('aria-pressed')).toBe('true')
  })
})

describe('joining from All projects', () => {
  beforeEach(() => {
    state.search = 'scope=all'
    state.projects = [memberProject, orphanProject]
  })

  it('marks a project the admin is not in and names its owner', () => {
    render(<ProjectsPage />)
    const card = screen.getByText('Orphan Cut').closest('a')!
    expect(within(card).getByText('Not a member')).toBeTruthy()
    expect(within(card).getByText('Owner: Alice')).toBeTruthy()
  })

  it('asks to join, calls the endpoint, then opens the project', async () => {
    render(<ProjectsPage />)
    fireEvent.click(screen.getByText('Orphan Cut'))

    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByText('Join Orphan Cut as owner?')).toBeTruthy()
    expect(within(dialog).getByText("You'll appear in its People list.")).toBeTruthy()
    fireEvent.click(within(dialog).getByRole('button', { name: 'Join as owner' }))

    await waitFor(() => expect(nav.push).toHaveBeenCalledWith('/projects/p-orphan'))
    expect(api.post).toHaveBeenCalledWith('/admin/projects/p-orphan/join')
    expect(toast.success).toHaveBeenCalled()
  })

  it('keeps the dialog open with the reason when joining fails', async () => {
    api.post.mockRejectedValueOnce(new Error('Project not found'))
    render(<ProjectsPage />)
    fireEvent.click(screen.getByText('Orphan Cut'))
    const dialog = await screen.findByRole('dialog')
    fireEvent.click(within(dialog).getByRole('button', { name: 'Join as owner' }))

    expect(await within(dialog).findByRole('alert')).toHaveProperty('textContent', 'Project not found')
    expect(nav.push).not.toHaveBeenCalled()
  })

  it('leaves cards the admin is already a member of alone', () => {
    render(<ProjectsPage />)
    const card = screen.getByText('Shared Reel').closest('a')!
    expect(within(card).queryByText('Not a member')).toBeNull()
    expect(card.getAttribute('href')).toBe('/projects/p-member')
    // Stop jsdom following the link; the page itself must not intercept it.
    card.addEventListener('click', (e) => e.preventDefault())
    fireEvent.click(card)
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(api.post).not.toHaveBeenCalled()
  })
})
