'use client'

import * as React from 'react'
import { useUploadStore, type UploadFile } from '@/stores/upload-store'

/**
 * Calls `onLanded` once for each upload to `projectId` that was the only file of
 * its drop or pick (`soloNewAsset`), when its transfer ends: the move into
 * `processing`, or straight into `complete` for a file nothing transcodes.
 *
 * Only a move seen while mounted counts. A row that had already landed before
 * the caller mounted is not announced: the user was somewhere else when it did.
 * While `enabled` is false moves are still tracked but announce nothing, so
 * turning it on later does not replay them.
 *
 * Watches the store from outside React, as `useRefetchOnUploadStatus` does, so
 * progress writes never re-render the caller.
 */
export function useSoloUploadLanded(
  projectId: string,
  enabled: boolean,
  onLanded: (row: UploadFile) => void,
): void {
  const onLandedRef = React.useRef(onLanded)
  const enabledRef = React.useRef(enabled)
  React.useEffect(() => {
    onLandedRef.current = onLanded
    enabledRef.current = enabled
  }, [onLanded, enabled])

  React.useEffect(() => {
    // Every solo row of the project seen so far, and whether it had landed then.
    const seen = new Map<string, boolean>()
    let seenFiles: UploadFile[] | null = null

    const look = (files: UploadFile[], announce: boolean) => {
      if (files === seenFiles) return
      seenFiles = files
      for (const f of files) {
        if (!f.soloNewAsset || f.projectId !== projectId) continue
        // The transfer is over and the asset exists: transcoding, or nothing to transcode.
        const now = (f.status === 'processing' || f.status === 'complete') && !!f.assetId
        const before = seen.get(f.id)
        seen.set(f.id, now)
        if (announce && before === false && now && enabledRef.current) onLandedRef.current(f)
      }
    }

    look(useUploadStore.getState().files, false)
    return useUploadStore.subscribe((state) => look(state.files, true))
  }, [projectId])
}
