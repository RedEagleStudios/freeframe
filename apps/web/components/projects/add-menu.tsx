'use client'

import * as React from 'react'
import * as DropdownMenu from '@radix-ui/react-dropdown-menu'
import { FolderPlus, Link2 } from 'lucide-react'
import { MenuItem, menuContentClass, menuItemClass, useAfterClose } from '@/components/shared/item-menu'

/** The Add button's menu: New Folder or New Link, both in the folder shown.
 *  Each opens a dialog, so it runs once the menu has closed (see item-menu). */
export function AddMenu({
  trigger,
  onNewFolder,
  onNewLink,
}: {
  /** The button that opens the menu. */
  trigger: React.ReactNode
  onNewFolder: () => void
  onNewLink: () => void
}) {
  const { schedule, onCloseAutoFocus } = useAfterClose()
  return (
    <DropdownMenu.Root>
      <DropdownMenu.Trigger asChild>{trigger}</DropdownMenu.Trigger>
      <DropdownMenu.Portal>
        <DropdownMenu.Content
          align="end"
          sideOffset={6}
          className={menuContentClass}
          onCloseAutoFocus={onCloseAutoFocus}
        >
          <MenuItem className={menuItemClass} onSelect={() => schedule(onNewFolder)}>
            <FolderPlus className="h-4 w-4" />
            New Folder
          </MenuItem>
          <MenuItem className={menuItemClass} onSelect={() => schedule(onNewLink)}>
            <Link2 className="h-4 w-4" />
            New Link
          </MenuItem>
        </DropdownMenu.Content>
      </DropdownMenu.Portal>
    </DropdownMenu.Root>
  )
}
