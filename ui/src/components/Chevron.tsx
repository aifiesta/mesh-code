/** A real chevron. The ‹ › glyphs render at a different weight and optical
 *  size than the rest of the icon set, which made the rail toggle look like
 *  it belonged to a different app. */
export function Chevron({ dir = 'right' }: { dir?: 'left' | 'right' }) {
  return (
    <svg viewBox="0 0 16 16" width="13" height="13" aria-hidden
         fill="none" stroke="currentColor" strokeWidth="1.7"
         strokeLinecap="round" strokeLinejoin="round"
         style={{ transform: dir === 'left' ? 'rotate(180deg)' : undefined }}>
      <path d="M6 3.5 10.5 8 6 12.5" />
    </svg>
  )
}


/** The 10px disclosure caret — replaces the ▸/▾ TEXT glyphs that rendered
 *  at each row's font size and made the icon scale look random. Rotates
 *  90° when open, so open/closed is one element, one size, everywhere. */
export function Caret({ open = false }: { open?: boolean }) {
  return (
    <svg viewBox="0 0 16 16" width="10" height="10" aria-hidden
         fill="none" stroke="currentColor" strokeWidth="1.8"
         strokeLinecap="round" strokeLinejoin="round"
         style={{ transform: open ? 'rotate(90deg)' : undefined,
                  transition: 'transform .14s ease' }}>
      <path d="M6 3.5 10.5 8 6 12.5" />
    </svg>
  )
}


/** 13px chrome icons — one drawn family for panel buttons, replacing the
 *  ↻ ↗ × ⏎ text glyphs that each rendered at their row's font metrics. */
const chrome = (d: string) => ({ size = 13 }: { size?: number }) => (
  <svg viewBox="0 0 16 16" width={size} height={size} aria-hidden
       fill="none" stroke="currentColor" strokeWidth="1.5"
       strokeLinecap="round" strokeLinejoin="round">
    <path d={d} />
  </svg>
)
export const IconReload = chrome('M13.4 8a5.4 5.4 0 1 1-1.6-3.8M13.6 2.6v3.2h-3.2')
export const IconExternal = chrome('M6.5 3.5H3.5v9h9V9.5M9.5 2.5h4v4M13.2 2.8 8 8')
export const IconX = chrome('M4 4l8 8M12 4l-8 8')
export const IconSend = chrome('M13 3v5a2.5 2.5 0 0 1-2.5 2.5H3.5M6.5 7 3 10.5 6.5 14')
export const IconStopSq = chrome('M4.5 4.5h7v7h-7z')
