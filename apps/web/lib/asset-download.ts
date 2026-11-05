import { api } from '@/lib/api'
import type { DownloadOption } from '@/types'

/**
 * What a version of an asset can be downloaded as: the original, then the
 * transcode. `versionId` left out, the latest ready version.
 */
export function fetchAssetDownloadOptions(assetId: string, versionId?: string): Promise<DownloadOption[]> {
  const query = versionId ? `?version_id=${encodeURIComponent(versionId)}` : ''
  return api.get<DownloadOption[]>(`/assets/${assetId}/downloads${query}`)
}

/**
 * Download an asset. `rendition` is an id from fetchAssetDownloadOptions;
 * left out, the server serves the transcode where there is one. `versionId`
 * left out, the latest ready version. `preferOriginal` (bulk downloads) asks
 * for the original when the instance allows originals.
 */
export async function downloadAsset(
  assetId: string,
  { rendition, versionId, preferOriginal = false }: {
    rendition?: string; versionId?: string; preferOriginal?: boolean
  } = {},
): Promise<void> {
  let query = rendition ? `&rendition=${encodeURIComponent(rendition)}` : ''
  if (versionId) query += `&version_id=${encodeURIComponent(versionId)}`
  if (preferOriginal) query += '&prefer_original=true'
  const data = await api.get<{ url: string }>(`/assets/${assetId}/stream?download=true${query}`)
  if (!data?.url) return
  // An iframe rather than navigation, so several downloads in a row (bulk)
  // do not cancel each other, and the page is never left.
  const iframe = document.createElement('iframe')
  iframe.style.display = 'none'
  iframe.src = data.url
  document.body.appendChild(iframe)
  setTimeout(() => iframe.remove(), 30000)
}
