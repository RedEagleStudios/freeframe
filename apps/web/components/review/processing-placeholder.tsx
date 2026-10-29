"use client";

import { Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";

/**
 * What the viewer shows for a version that is still being prepared and cannot
 * be previewed: no browser-playable original, or one the browser failed to
 * decode (ProRes, HEVC on most desktops). `percent` is the last transcode
 * progress the page heard about, when it has a live event stream.
 */
export function ProcessingPlaceholder({
  percent,
  className,
}: {
  percent?: number | null;
  className?: string;
}) {
  const shown = typeof percent === "number" && Number.isFinite(percent)
    ? Math.max(0, Math.min(100, Math.round(percent)))
    : null;
  return (
    <div className={cn("flex-1 flex items-center justify-center", className)}>
      <div className="flex flex-col items-center gap-4 text-center px-6">
        <div className="h-12 w-12 rounded-full bg-accent/10 flex items-center justify-center">
          <Loader2 className="h-6 w-6 animate-spin text-accent" />
        </div>
        <div>
          <p className="text-sm font-medium text-text-primary">Processing asset</p>
          <p className="text-xs text-text-tertiary mt-1">
            {shown !== null
              ? `${shown}% processed`
              : "This may take a few minutes depending on file size."}
          </p>
        </div>
      </div>
    </div>
  );
}
