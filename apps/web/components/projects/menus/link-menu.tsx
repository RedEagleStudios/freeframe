'use client'

import * as React from 'react'
import { ExternalLink, Pencil, Trash2 } from 'lucide-react'
import { MenuAction, MenuSeparator } from '@/components/shared/item-menu'
import type { ProjectLink } from '@/types'

/** A link's menu: the same in the tree, the grid and the list, from its ⋯
 *  button and on right-click. Edit and Delete only when their action is given. */
export function LinkMenuItems({
  link,
  onEdit,
  onDelete,
}: {
  link: ProjectLink
  onEdit?: (link: ProjectLink) => void
  onDelete?: (link: ProjectLink) => Promise<void>
}) {
  return (
    <>
      {/* Inside the click: a tab opened after the menu has closed is a popup
          the browser may block. */}
      <MenuAction
        icon={ExternalLink}
        immediate
        onSelect={() => window.open(link.url, '_blank', 'noopener,noreferrer')}
      >
        Open in New Tab
      </MenuAction>
      {(onEdit || onDelete) && <MenuSeparator />}
      {onEdit && (
        <MenuAction icon={Pencil} onSelect={() => onEdit(link)}>
          Edit
        </MenuAction>
      )}
      {onDelete && (
        <MenuAction
          icon={Trash2}
          danger
          onSelect={async () => {
            if (confirm(`Delete link "${link.name}"?`)) await onDelete(link)
          }}
        >
          Delete
        </MenuAction>
      )}
    </>
  )
}
