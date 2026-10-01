/**
 * The real Mesh brand assets, straight from marketing/Branding/mesh_api_logo_v2.
 *
 * These are served as files rather than inlined: they are ~14–24KB each, the
 * engine already serves the UI from one origin (so `img-src 'self'` covers
 * them), and keeping them as files means dropping in an updated brand asset
 * is a copy, not a code change.
 *
 * Theme handling is CSS, not JavaScript. The wordmark ships as separate
 * light and dark artwork, so both are rendered and `prefers-color-scheme`
 * picks one. Doing it in JS would mean a flash of the wrong artwork on every
 * launch, and would not follow the OS if the user changed theme mid-session.
 *
 * The mark itself is a fixed purple gradient that reads correctly on both
 * grounds, so it needs no variant — which is also why it, not the wordmark,
 * is what appears in tight spots like the tab icon.
 *
 * NOTE: the wordmark artwork ALREADY INCLUDES the mark. Never place <Mark/>
 * beside <Wordmark/> — that draws the hexagon twice.
 */

export function Mark({ className, animate = false }: {
  className?: string
  animate?: boolean
}) {
  return (
    <img
      src="/brand/mark.svg"
      alt=""
      aria-hidden
      draggable={false}
      className={`mark ${animate ? 'animate' : ''} ${className ?? ''}`}
    />
  )
}

export function Wordmark({ className }: { className?: string }) {
  return (
    <span className={`wordmark-img ${className ?? ''}`} role="img" aria-label="mesh_api">
      <img src="/brand/wordmark-light.svg" alt="" draggable={false} className="only-light" />
      <img src="/brand/wordmark-dark.svg" alt="" draggable={false} className="only-dark" />
    </span>
  )
}

/** Mark + wordmark + the product name, as one lockup. */
export function Lockup({ className }: { className?: string }) {
  return (
    <span className={`lockup ${className ?? ''}`}>
      <Mark className="lockup-mark" />
      <Wordmark className="lockup-word" />
      <span className="lockup-product">Code</span>
    </span>
  )
}
