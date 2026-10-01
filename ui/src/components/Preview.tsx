import { useEffect, useRef, useState } from 'react'
import { IconExternal, IconReload } from './Chevron'

/**
 * Live preview of a dev server the agent started.
 *
 * It is an <iframe>, not an Electron <webview>: webview needs
 * `webviewTag: true` on a window that renders model output, which is real
 * attack surface for a convenience. The iframe is sandboxed, and CSP
 * `frame-src` is narrowed to loopback, so the only thing this frame can
 * ever load is a server running on this machine.
 *
 * `key` is bumped on reload rather than touching contentWindow.location —
 * cross-origin means we cannot reach into the frame at all, so remounting
 * is the only reload we actually have.
 */
export function Preview({ servers }: {
  servers: { pid: number; port: number; url: string; cmd: string }[]
}) {
  const [active, setActive] = useState(servers[0]?.url ?? '')
  const [nonce, setNonce] = useState(0)
  const [loading, setLoading] = useState(true)
  const frame = useRef<HTMLIFrameElement>(null)

  useEffect(() => {
    if (!servers.some((s) => s.url === active)) setActive(servers[0]?.url ?? '')
  }, [servers, active])

  useEffect(() => { setLoading(true) }, [active, nonce])

  if (!active) return null

  return (
    <section className="preview">
      <div className="preview-bar">
        {servers.length > 1 ? (
          <select value={active} onChange={(e) => setActive(e.target.value)}>
            {servers.map((s) => <option key={s.pid} value={s.url}>:{s.port}</option>)}
          </select>
        ) : (
          <span className="preview-url" title={active}>{active}</span>
        )}
        <div className="spacer" />
        <button title="Reload" onClick={() => setNonce((n) => n + 1)}><IconReload /></button>
        <button title="Open in browser" onClick={() => window.open(active, '_blank', 'noopener')}><IconExternal /></button>
      </div>
      <div className="preview-frame">
        {loading && <div className="preview-loading"><span /></div>}
        <iframe
          key={`${active}#${nonce}`}
          ref={frame}
          src={active}
          title="Live preview"
          sandbox="allow-scripts allow-forms allow-same-origin"
          onLoad={() => setLoading(false)}
        />
      </div>
    </section>
  )
}
