'use client'

import useSWR from 'swr'
import { api } from '@/lib/api'
import type { Folder, FolderDuplicateResponse, FolderTreeNode, ProjectLink, TrashResponse } from '@/types'

export function useFolders(projectId: string) {
  const { data: tree, mutate: mutateTree } = useSWR<FolderTreeNode[]>(
    projectId ? `/projects/${projectId}/folder-tree` : null,
    (key: string) => api.get<FolderTreeNode[]>(key),
  )
  // Links are hidden with a trashed folder and come back with it, so they are
  // refreshed whenever the tree is.
  const { data: links, mutate: mutateLinks } = useSWR<ProjectLink[]>(
    projectId ? `/projects/${projectId}/links` : null,
    (key: string) => api.get<ProjectLink[]>(key),
  )

  async function refresh(): Promise<void> {
    await Promise.all([mutateTree(), mutateLinks()])
  }

  async function createLink(name: string, url: string, folderId: string | null): Promise<ProjectLink> {
    const link = await api.post<ProjectLink>(`/projects/${projectId}/links`, {
      name,
      url,
      folder_id: folderId,
    })
    await refresh() // a link counts toward its folder's items
    return link
  }

  async function updateLink(linkId: string, changes: { name?: string; url?: string }): Promise<ProjectLink> {
    const link = await api.patch<ProjectLink>(`/links/${linkId}`, changes)
    await mutateLinks()
    return link
  }

  async function deleteLink(linkId: string): Promise<void> {
    await api.delete(`/links/${linkId}`)
    await refresh()
  }

  async function createFolder(name: string, parentId?: string | null): Promise<Folder> {
    const folder = await api.post<Folder>(`/projects/${projectId}/folders`, {
      name,
      parent_id: parentId ?? null,
    })
    await mutateTree()
    return folder
  }

  async function renameFolder(folderId: string, name: string): Promise<Folder> {
    const folder = await api.patch<Folder>(`/folders/${folderId}`, { name })
    await mutateTree()
    return folder
  }

  async function moveFolder(folderId: string, targetParentId: string | null): Promise<void> {
    await api.patch(`/folders/${folderId}`, { parent_id: targetParentId })
    await mutateTree()
  }

  async function deleteFolder(folderId: string): Promise<void> {
    await api.delete(`/folders/${folderId}`)
    await refresh()
  }

  async function duplicateFolder(folderId: string): Promise<FolderDuplicateResponse> {
    const result = await api.post<FolderDuplicateResponse>(`/folders/${folderId}/duplicate`)
    await refresh()
    return result
  }

  async function moveAsset(assetId: string, folderId: string | null): Promise<void> {
    await api.patch(`/assets/${assetId}/move`, { folder_id: folderId })
    await mutateTree()
  }

  async function bulkMove(
    assetIds: string[],
    folderIds: string[],
    targetFolderId: string | null,
  ): Promise<void> {
    await api.post(`/projects/${projectId}/bulk-move`, {
      asset_ids: assetIds,
      folder_ids: folderIds,
      target_folder_id: targetFolderId,
    })
    await mutateTree()
  }

  async function restoreAsset(assetId: string): Promise<void> {
    await api.post(`/assets/${assetId}/restore`)
    await mutateTree()
  }

  async function restoreFolder(folderId: string): Promise<void> {
    await api.post(`/folders/${folderId}/restore`)
    await refresh()
  }

  return {
    tree: tree ?? [],
    mutateTree,
    createFolder,
    renameFolder,
    moveFolder,
    deleteFolder,
    duplicateFolder,
    links: links ?? [],
    createLink,
    updateLink,
    deleteLink,
    moveAsset,
    bulkMove,
    restoreAsset,
    restoreFolder,
  }
}

export function useTrash(projectId: string) {
  const { data, mutate, isLoading } = useSWR<TrashResponse>(
    projectId ? `/projects/${projectId}/trash` : null,
    (key: string) => api.get<TrashResponse>(key),
  )

  return {
    trash: data ?? { folders: [], assets: [] },
    isLoading,
    mutateTrash: mutate,
  }
}
