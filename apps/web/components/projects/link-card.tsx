'use client'

import * as React from 'react'
import { ExternalLink, Link2, MoreHorizontal } from 'lucide-react'
import { ContextMenuArea, DotsMenu } from '@/components/shared/item-menu'
import { LinkMenuItems } from './menus/link-menu'
import type { ProjectLink } from '@/types'

/** What a link points at, short enough for a card: `outline.example.com`. */
export function linkHost(url: string): string {
  try {
    return new URL(url).host
  } catch {
    return url
  }
}

/** A link in the grid, sized like a folder card. The card is the link: a click
 *  (or a middle click) opens the page in a new tab. */
export function LinkCard({
  link,
  onEdit,
  onDelete,
}: {
  link: ProjectLink
  onEdit?: (link: ProjectLink) => void
  onDelete?: (link: ProjectLink) => Promise<void>
}) {
  const items = <LinkMenuItems link={link} onEdit={onEdit} onDelete={onDelete} />
  return (
    <ContextMenuArea items={items}>
      <div className="group relative rounded-lg border border-border bg-bg-tertiary/50 transition-all hover:border-border-focus hover:scale-[1.01]">
        <a
          href={link.url}
          target="_blank"
          rel="noopener noreferrer"
          title={link.url}
          className="block"
        >
          <div className="aspect-[4/3] flex items-center justify-center bg-bg-tertiary rounded-t-lg">
            <Link2 className="h-12 w-12 text-text-tertiary/50" />
          </div>
          <div className="px-3 py-2 pr-10">
            <p className="text-sm font-medium text-text-primary truncate">{link.name}</p>
            <p className="flex items-center gap-1 text-xs text-text-tertiary mt-0.5">
              <ExternalLink className="h-3 w-3 shrink-0" />
              <span className="truncate">{linkHost(link.url)}</span>
            </p>
          </div>
        </a>
        <div className="absolute right-3 bottom-[18px]">
          <DotsMenu items={items}>
            <button
              aria-label={`${link.name} menu`}
              className="opacity-0 group-hover:opacity-100 data-[state=open]:opacity-100 flex items-center justify-center h-6 w-6 rounded hover:bg-bg-hover transition-opacity"
            >
              <MoreHorizontal className="h-3.5 w-3.5 text-text-tertiary" />
            </button>
          </DotsMenu>
        </div>
      </div>
    </ContextMenuArea>
  )
}
