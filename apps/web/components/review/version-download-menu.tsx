'use client'

import * as React from 'react'
import * as DropdownMenu from '@radix-ui/react-dropdown-menu'
import { Download } from 'lucide-react'
import { DownloadMenu, DownloadSubmenu } from '@/components/shared/download-menu'
import { MenuLabel, menuContentClass } from '@/components/shared/item-menu'
import { useToast } from '@/components/shared/toast'
import { downloadAsset, fetchAssetDownloadOptions } from '@/lib/asset-download'
import type { AssetVersion } from '@/types'

const triggerClass =
  'inline-flex items-center gap-1.5 rounded-md px-2.5 h-8 text-xs font-medium border border-border text-text-secondary hover:text-text-primary hover:bg-bg-hover transition-colors'

/**
 * The asset page's Download button. Each ready version is a submenu of what it
 * can be downloaded as (the original, the transcode), newest first, so an older
 * version can be had without switching the player to it. With one ready
 * version the button opens that version's choices directly. Versions still
 * uploading or processing have nothing to download and are left out.
 */
export function VersionDownloadMenu({
  assetId,
  versions,
  currentVersionId,
}: {
  assetId: string
  versions: AssetVersion[]
  /** The version the player shows, marked in the list. */
  currentVersionId?: string
}) {
  const toast = useToast()
  const ready = React.useMemo(
    () =>
      versions
        .filter((v) => v.processing_status === 'ready')
        .sort((a, b) => b.version_number - a.version_number),
    [versions],
  )

  const download = React.useCallback(
    async (versionId: string, rendition?: string) => {
      try {
        await downloadAsset(assetId, { versionId, rendition })
      } catch (err) {
        toast.error(err instanceof Error && err.message ? err.message : 'Download failed')
      }
    },
    [assetId, toast],
  )

  if (ready.length === 0) return null

  const trigger = (
    <button className={triggerClass} title="Download" aria-label="Download">
      <Download className="h-3.5 w-3.5" />
      <span className="hidden sm:inline">Download</span>
    </button>
  )

  if (ready.length === 1) {
    const only = ready[0]
    return (
      <DownloadMenu
        load={() => fetchAssetDownloadOptions(assetId, only.id)}
        onPick={(rendition) => void download(only.id, rendition)}
      >
        {trigger}
      </DownloadMenu>
    )
  }

  const latestId = ready[0].id
  return (
    <DropdownMenu.Root>
      <DropdownMenu.Trigger asChild>{trigger}</DropdownMenu.Trigger>
      <DropdownMenu.Portal>
        <DropdownMenu.Content align="end" sideOffset={4} className={menuContentClass}>
          <MenuLabel>Download version</MenuLabel>
          {ready.map((v) => (
            <DownloadSubmenu
              key={v.id}
              load={() => fetchAssetDownloadOptions(assetId, v.id)}
              onPick={(rendition) => void download(v.id, rendition)}
            >
              <span className="flex-1 text-text-primary">v{v.version_number}</span>
              <span className="text-xs text-text-tertiary">
                {[v.id === currentVersionId && 'Viewing', v.id === latestId && 'Latest']
                  .filter(Boolean)
                  .join(' · ')}
              </span>
            </DownloadSubmenu>
          ))}
        </DropdownMenu.Content>
      </DropdownMenu.Portal>
    </DropdownMenu.Root>
  )
}
