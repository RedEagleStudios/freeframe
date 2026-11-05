import React from 'react'
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ToastProvider } from '@/components/shared/toast'
import type { AssetVersion, DownloadOption } from '@/types'

const lib = vi.hoisted(() => ({
  fetchAssetDownloadOptions: vi.fn(),
  downloadAsset: vi.fn(),
}))
vi.mock('@/lib/asset-download', () => lib)

import { VersionDownloadMenu } from '../version-download-menu'

// The asset page's Download button picks a version as well as a file, so an
// older version can be downloaded without switching the player to it.

const OPTIONS: DownloadOption[] = [
  { id: 'original', label: 'Original', format: 'MOV', size_bytes: 1024 },
  { id: 'transcode', label: '1080p', format: 'MP4', size_bytes: null },
]

function version(n: number, status: AssetVersion['processing_status'] = 'ready'): AssetVersion {
  return { id: `v${n}-id`, version_number: n, processing_status: status } as AssetVersion
}

function renderMenu(versions: AssetVersion[], currentVersionId?: string) {
  return render(
    <ToastProvider>
      <VersionDownloadMenu assetId="a1" versions={versions} currentVersionId={currentVersionId} />
    </ToastProvider>,
  )
}

beforeEach(() => {
  lib.fetchAssetDownloadOptions.mockReset().mockResolvedValue(OPTIONS)
  lib.downloadAsset.mockReset().mockResolvedValue(undefined)
})

describe('VersionDownloadMenu', () => {
  it('downloads the file picked from an older version, not the latest', async () => {
    renderMenu([version(1), version(2), version(3, 'processing')], 'v2-id')
    await userEvent.click(screen.getByRole('button', { name: 'Download' }))

    // Newest ready first; a version still processing has nothing to offer.
    const entries = (await screen.findAllByRole('menuitem')).map((el) => el.textContent)
    expect(entries).toEqual(['v2Viewing · Latest', 'v1'])

    await userEvent.click(screen.getByRole('menuitem', { name: /^v1/ }))
    await userEvent.click(await screen.findByRole('menuitem', { name: /Original/ }))

    expect(lib.fetchAssetDownloadOptions).toHaveBeenCalledWith('a1', 'v1-id')
    expect(lib.downloadAsset).toHaveBeenCalledWith('a1', { versionId: 'v1-id', rendition: 'original' })
  })

  it("opens a single ready version's choices directly", async () => {
    renderMenu([version(1), version(2, 'failed')])
    await userEvent.click(screen.getByRole('button', { name: 'Download' }))
    await userEvent.click(await screen.findByRole('menuitem', { name: /1080p/ }))

    expect(lib.downloadAsset).toHaveBeenCalledWith('a1', { versionId: 'v1-id', rendition: 'transcode' })
  })

  it('is not offered before any version is ready', () => {
    renderMenu([version(1, 'processing')])
    expect(screen.queryByRole('button', { name: 'Download' })).toBeNull()
  })

  it('says why when the download is refused', async () => {
    lib.downloadAsset.mockRejectedValue(new Error('Asset version is not ready yet'))
    renderMenu([version(1)])
    await userEvent.click(screen.getByRole('button', { name: 'Download' }))
    await userEvent.click(await screen.findByRole('menuitem', { name: /Original/ }))

    await waitFor(() => expect(screen.getByText('Asset version is not ready yet')).toBeTruthy())
  })
})
