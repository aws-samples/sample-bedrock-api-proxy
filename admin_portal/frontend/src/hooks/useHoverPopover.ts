import { useEffect, useRef, useState } from 'react';

export interface HoverPopoverOptions {
  /** Popover width in px; used to clamp horizontally inside the viewport. */
  width: number;
  /** Approximate popover height in px; used to flip above the anchor when needed. */
  height: number;
  /** Delay before opening, so requests don't fire while scanning across rows. */
  openDelayMs?: number;
  /**
   * Delay before closing after the pointer leaves the anchor. 0 closes
   * immediately (non-interactive popovers). A small delay lets the pointer
   * cross the gap into an interactive popover without it disappearing.
   */
  closeDelayMs?: number;
}

export interface HoverPopoverPosition {
  top: number;
  left: number;
}

/**
 * Shared open/close + positioning logic for hover popovers rendered through a
 * portal with fixed positioning (see UsageHoverChart, SpeedTestHoverChart).
 *
 * Attach `anchorRef` and the two mouse handlers to the wrapping element.
 * `pos` is null while closed; when non-null, render the popover at that
 * fixed position.
 */
export function useHoverPopover({
  width,
  height,
  openDelayMs = 200,
  closeDelayMs = 0,
}: HoverPopoverOptions) {
  const anchorRef = useRef<HTMLDivElement>(null);
  const openTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const closeTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [pos, setPos] = useState<HoverPopoverPosition | null>(null);

  useEffect(() => {
    // Clear any pending timers on unmount
    return () => {
      if (openTimerRef.current) clearTimeout(openTimerRef.current);
      if (closeTimerRef.current) clearTimeout(closeTimerRef.current);
    };
  }, []);

  const handleMouseEnter = () => {
    if (closeTimerRef.current) {
      clearTimeout(closeTimerRef.current);
      closeTimerRef.current = null;
    }
    if (openTimerRef.current) clearTimeout(openTimerRef.current);
    openTimerRef.current = setTimeout(() => {
      const rect = anchorRef.current?.getBoundingClientRect();
      if (!rect) return;

      // Prefer below the cell; flip above when there is not enough room.
      let top = rect.bottom + 8;
      if (top + height > window.innerHeight - 8) {
        top = rect.top - height - 8;
      }
      // Clamp horizontally inside the viewport.
      let left = rect.left;
      if (left + width > window.innerWidth - 8) {
        left = window.innerWidth - width - 8;
      }
      setPos({ top: Math.max(8, top), left: Math.max(8, left) });
    }, openDelayMs);
  };

  const handleMouseLeave = () => {
    if (openTimerRef.current) {
      clearTimeout(openTimerRef.current);
      openTimerRef.current = null;
    }
    if (closeDelayMs <= 0) {
      setPos(null);
      return;
    }
    if (closeTimerRef.current) clearTimeout(closeTimerRef.current);
    closeTimerRef.current = setTimeout(() => {
      closeTimerRef.current = null;
      setPos(null);
    }, closeDelayMs);
  };

  return { anchorRef, pos, handleMouseEnter, handleMouseLeave };
}
