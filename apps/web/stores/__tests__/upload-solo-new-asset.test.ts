/**
 * Which rows are the only new file of their drop or pick: what a streaming
 * project's page opens Create Share Link for once the file is up.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'

vi.mock('@/lib/api', async (orig) => ({
  ...(await orig<typeof import('@/lib/api')>()),
  api: { post: vi.fn(), get: vi.fn(), patch: vi.fn() },
}))
vi.mock('swr', () => ({ mutate: vi.fn() }))

import { api } from '@/lib/api'
import { useUploadStore, __uploadQueueTesting as testing } from '../upload-store'
import { signInAs } from '@/test/upload-session'
import { fakeServer, fileOf, files } from '@/test/upload-queue-server'

beforeEach(() => {
  vi.clearAllMocks()
  testing.reset()
  window.localStorage.clear()
  useUploadStore.setState({ files: [], dropNotes: [], panelOpen: false })
  signInAs('user-1')
  vi.mocked(api.get).mockImplementation(() => new Promise(() => {}) as never)
  fakeServer(() => 'wait')
})

afterEach(() => {
  vi.restoreAllMocks()
  testing.reset()
})

describe('soloNewAsset', () => {
  it('marks a drop of one file', () => {
    useUploadStore.getState().startUploads([{ file: fileOf('a.mov'), assetName: 'a' }], 'p1')

    expect(files().map((f) => f.soloNewAsset)).toEqual([true])
  })

  it('marks no file of a drop of several', () => {
    useUploadStore.getState().startUploads(
      [{ file: fileOf('a.mov'), assetName: 'a' }, { file: fileOf('b.mov'), assetName: 'b' }],
      'p1',
    )

    expect(files().map((f) => f.soloNewAsset)).toEqual([undefined, undefined])
  })

  it('does not mark a new version', () => {
    useUploadStore.getState().startVersionUpload(fileOf('a.mov'), 'asset-1', 'a', 'p1')

    expect(files()[0].soloNewAsset).toBeUndefined()
  })
})
