/**
 * Once its transfer is over a row offers to open its asset, while it is still
 * processing too. Before that there is no asset to open.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest'

vi.stubGlobal(
  'IntersectionObserver',
  class {
    observe() {}
    unobserve() {}
    disconnect() {}
  },
)
import { fireEvent, render, screen } from '@testing-library/react'

vi.mock('@/lib/api', async (orig) => ({
  ...(await orig<typeof import('@/lib/api')>()),
  api: { post: vi.fn(), get: vi.fn().mockResolvedValue([]) },
}))

import { UploadsPanel } from '../uploads-panel'
import { useUploadStore, type UploadFile } from '@/stores/upload-store'
import { signInAs } from '@/test/upload-session'

beforeEach(() => signInAs('u1'))

function row(patch: Partial<UploadFile> = {}): UploadFile {
  return {
    id: 'row-1', fileName: 'clip.mp4', fileSize: 1024, fileType: 'video/mp4',
    projectId: 'p1', projectName: 'Minecraft', assetName: 'clip',
    progress: 100, processingProgress: 0, status: 'processing',
    assetId: 'a1', versionId: 'v1', createdAt: Date.now(), ownerId: 'u1',
    ...patch,
  }
}

function open(file: UploadFile) {
  useUploadStore.setState({
    files: [file], panelOpen: true, historyLoaded: true, historyHasMore: false,
  })
  render(<UploadsPanel />)
}

const openLink = () => screen.queryByRole('link', { name: 'Open clip' })

describe('Open in the uploads panel', () => {
  it.each([['processing'], ['complete']] as const)('a %s row links to its asset in the viewer', (status) => {
    open(row({ status }))
    // A ready row has left the Active tab the panel opens on.
    fireEvent.click(screen.getByRole('button', { name: /^All/ }))

    expect(openLink()?.getAttribute('href')).toBe('/projects/p1/assets/a1')
  })

  it('closes the panel on the way', () => {
    open(row())

    fireEvent.click(openLink()!)

    expect(useUploadStore.getState().panelOpen).toBe(false)
  })

  it('a row still sending has nothing to open yet', () => {
    open(row({ status: 'uploading', progress: 40 }))

    expect(openLink()).toBeNull()
  })
})
