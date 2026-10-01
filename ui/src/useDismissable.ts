import { useEffect, type RefObject } from 'react'

/** Broadcast so every transient overlay closes when a modal takes over. */
export const DISMISS_EVENT = 'mesh:dismiss-overlays'
export const dismissOverlays = () => window.dispatchEvent(new Event(DISMISS_EVENT))

/**
 * One dismissal contract for every popover and menu.
 *
 * These were hand-rolled per component and had drifted: the control
 * popovers and the account menu closed on an outside click, the composer's
 * mode menu closed on nothing at all — open it, click away, and it stayed.
 * Worse, opening the command palette left whatever was already open
 * underneath it, so overlays stacked instead of replacing one another.
 *
 * Three ways out, applied uniformly: click outside, press Escape, or
 * another surface claims the screen.
 */
export function useDismissable(
  open: boolean,
  close: () => void,
  ref: RefObject<HTMLElement | null>,
) {
  useEffect(() => {
    if (!open) return
    const away = (e: Event) => {
      if (ref.current && !ref.current.contains(e.target as Node)) close()
    }
    const key = (e: KeyboardEvent) => { if (e.key === 'Escape') close() }
    // pointerdown, not mousedown: it covers touch and pen too, and it lands
    // before the click that would otherwise re-open what we just closed.
    document.addEventListener('pointerdown', away)
    window.addEventListener('keydown', key)
    window.addEventListener(DISMISS_EVENT, close)
    return () => {
      document.removeEventListener('pointerdown', away)
      window.removeEventListener('keydown', key)
      window.removeEventListener(DISMISS_EVENT, close)
    }
  }, [open, close, ref])
}
