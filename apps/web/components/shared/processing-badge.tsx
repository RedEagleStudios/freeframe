import { Cog } from 'lucide-react'
import { cn } from '@/lib/utils'

/**
 * A version still being transcoded: the uploads panel's row badge, and the
 * same badge on the asset's card and list row in the project grid.
 */
export function ProcessingBadge({ className }: { className?: string }) {
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1 rounded-full bg-amber-500/10 px-2 py-0.5 text-[10px] font-medium text-amber-400',
        className,
      )}
    >
      <Cog className="h-2.5 w-2.5 animate-spin" />
      Processing
    </span>
  )
}
