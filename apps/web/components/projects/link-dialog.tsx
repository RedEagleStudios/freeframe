'use client'

import * as React from 'react'
import * as Dialog from '@radix-ui/react-dialog'
import { X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { ApiError } from '@/lib/api'

/** An address as typed, made absolute: `outline.example.com/doc/x` gains
 *  `https://`. Null when it still is not an http(s) URL with a host. */
export function normalizeLinkUrl(raw: string): string | null {
  const trimmed = raw.trim()
  if (!trimmed) return null
  const withScheme = /^[a-z][a-z0-9+.-]*:/i.test(trimmed) ? trimmed : `https://${trimmed}`
  try {
    const url = new URL(withScheme)
    if ((url.protocol !== 'http:' && url.protocol !== 'https:') || !url.hostname) return null
    return withScheme
  } catch {
    return null
  }
}

interface LinkDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  title: string
  submitLabel: string
  defaultName?: string
  defaultUrl?: string
  /** Throwing keeps the dialog open with the error shown. */
  onSubmit: (name: string, url: string) => Promise<void>
}

export function LinkDialog({
  open,
  onOpenChange,
  title,
  submitLabel,
  defaultName = '',
  defaultUrl = '',
  onSubmit,
}: LinkDialogProps) {
  const [name, setName] = React.useState(defaultName)
  const [url, setUrl] = React.useState(defaultUrl)
  const [error, setError] = React.useState<string | null>(null)
  const [saving, setSaving] = React.useState(false)
  const nameRef = React.useRef<HTMLInputElement>(null)

  // Reset as the dialog opens, in the same render (see NameDialog).
  const [wasOpen, setWasOpen] = React.useState(open)
  if (open !== wasOpen) {
    setWasOpen(open)
    if (open) {
      setName(defaultName)
      setUrl(defaultUrl)
      setError(null)
    }
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    const trimmedName = name.trim()
    const normalized = normalizeLinkUrl(url)
    if (!trimmedName) return
    if (!normalized) {
      setError('Enter a web address, like https://outline.example.com/doc/…')
      return
    }
    setSaving(true)
    try {
      await onSubmit(trimmedName, normalized)
      onOpenChange(false)
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'Could not save the link')
    } finally {
      setSaving(false)
    }
  }

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-black/50 backdrop-blur-sm data-[state=open]:animate-in data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0" />
        <Dialog.Content
          className="fixed left-1/2 top-1/2 z-50 w-full max-w-sm -translate-x-1/2 -translate-y-1/2 rounded-xl border border-border bg-bg-secondary p-5 shadow-xl data-[state=open]:animate-in data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0 data-[state=closed]:zoom-out-95 data-[state=open]:zoom-in-95"
          onOpenAutoFocus={(e) => {
            e.preventDefault()
            nameRef.current?.focus()
            nameRef.current?.select()
          }}
        >
          <Dialog.Close className="absolute right-3 top-3 text-text-tertiary hover:text-text-primary transition-colors">
            <X className="h-4 w-4" />
          </Dialog.Close>

          <Dialog.Title className="text-sm font-semibold text-text-primary">{title}</Dialog.Title>
          <Dialog.Description className="mt-1 text-xs text-text-tertiary">
            Shown in the folder tree. Clicking it opens the page in a new tab.
          </Dialog.Description>

          <form onSubmit={handleSubmit} className="mt-3 space-y-3">
            <Input
              ref={nameRef}
              aria-label="Link name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Name, e.g. Script"
              autoComplete="off"
              maxLength={255}
            />
            <Input
              aria-label="Link address"
              value={url}
              onChange={(e) => {
                setUrl(e.target.value)
                setError(null)
              }}
              placeholder="https://outline.example.com/doc/…"
              autoComplete="off"
              inputMode="url"
              maxLength={2000}
            />
            {error && <p className="text-xs text-status-error">{error}</p>}
            <div className="flex justify-end gap-2">
              <Button type="button" variant="secondary" size="sm" onClick={() => onOpenChange(false)}>
                Cancel
              </Button>
              <Button type="submit" size="sm" disabled={!name.trim() || !url.trim() || saving}>
                {submitLabel}
              </Button>
            </div>
          </form>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}
