/**
 * A streaming project's page opens Create Share Link when a lone new upload
 * lands. Only the landing itself counts: not a drop of several files, not a
 * row that had landed before the page was open, not a project with the
 * feature off, and never twice for one row.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { act, renderHook } from '@testing-library/react'
import { useUploadStore, type UploadFile } from '@/stores/upload-store'
import { useSoloUploadLanded } from '../use-solo-upload-landed'

function row(id: string, patch: Partial<UploadFile> = {}): UploadFile {
  return {
    id, fileName: `${id}.mp4`, fileSize: 1, fileType: 'video/mp4',
    projectId: 'p', assetName: id, progress: 0, processingProgress: 0,
    status: 'uploading', createdAt: 0, soloNewAsset: true,
    ...patch,
  }
}

function patch(id: string, change: Partial<UploadFile>) {
  act(() => {
    useUploadStore.setState((s) => ({
      files: s.files.map((f) => (f.id === id ? { ...f, ...change } : f)),
    }))
  })
}

const landing = { status: 'processing' as const, assetId: 'asset-1', progress: 100 }

beforeEach(() => {
  useUploadStore.setState({ files: [] })
})

describe('useSoloUploadLanded', () => {
  it('announces a lone upload once, when its transfer ends', () => {
    useUploadStore.setState({ files: [row('solo')] })
    const onLanded = vi.fn()
    renderHook(() => useSoloUploadLanded('p', true, onLanded))

    patch('solo', landing)
    patch('solo', { processingProgress: 40 })
    patch('solo', { status: 'complete', processingProgress: 100 })

    expect(onLanded).toHaveBeenCalledOnce()
    expect(onLanded.mock.calls[0][0]).toMatchObject({ id: 'solo', assetId: 'asset-1' })
  })

  it('announces a file nothing transcodes when it goes straight to complete', () => {
    useUploadStore.setState({ files: [row('pdf', { fileType: 'application/pdf' })] })
    const onLanded = vi.fn()
    renderHook(() => useSoloUploadLanded('p', true, onLanded))

    patch('pdf', { status: 'complete', assetId: 'asset-2' })

    expect(onLanded).toHaveBeenCalledOnce()
  })

  it('stays quiet for a file of a larger drop', () => {
    useUploadStore.setState({ files: [row('one', { soloNewAsset: undefined })] })
    const onLanded = vi.fn()
    renderHook(() => useSoloUploadLanded('p', true, onLanded))

    patch('one', landing)

    expect(onLanded).not.toHaveBeenCalled()
  })

  it('stays quiet for a row that had already landed before the page was open', () => {
    useUploadStore.setState({ files: [row('old', landing)] })
    const onLanded = vi.fn()
    renderHook(() => useSoloUploadLanded('p', true, onLanded))

    patch('old', { status: 'complete' })

    expect(onLanded).not.toHaveBeenCalled()
  })

  it('stays quiet for another project’s upload', () => {
    useUploadStore.setState({ files: [row('elsewhere', { projectId: 'q' })] })
    const onLanded = vi.fn()
    renderHook(() => useSoloUploadLanded('p', true, onLanded))

    patch('elsewhere', landing)

    expect(onLanded).not.toHaveBeenCalled()
  })

  it('stays quiet while disabled, and does not replay the landing once enabled', () => {
    useUploadStore.setState({ files: [row('solo')] })
    const onLanded = vi.fn()
    const { rerender } = renderHook(({ on }) => useSoloUploadLanded('p', on, onLanded), {
      initialProps: { on: false },
    })

    patch('solo', landing)
    rerender({ on: true })
    patch('solo', { status: 'complete' })

    expect(onLanded).not.toHaveBeenCalled()
  })
})
