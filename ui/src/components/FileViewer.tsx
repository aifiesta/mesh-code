import { useEffect, useMemo, useState } from 'react'
import { createPortal } from 'react-dom'
import { highlightLines, langForFile, Line } from '../highlight'
import { Markdown } from './Markdown'

type File = { path: string; content: string; size?: number; binary?: boolean
              too_large?: boolean; lines?: number; error?: string; root?: string }

/** What a file can be shown AS. Code is the fallback for anything text. */
type Kind = 'markdown' | 'html' | 'pdf' | 'image' | 'text'
type Mode = 'preview' | 'code'

function kindOf(name: string): Kind {
  const ext = name.slice(name.lastIndexOf('.') + 1).toLowerCase()
  if (['md', 'mdx', 'markdown'].includes(ext)) return 'markdown'
  if (['html', 'htm'].includes(ext)) return 'html'
  if (ext === 'pdf') return 'pdf'
  if (['png', 'jpg', 'jpeg', 'gif', 'webp', 'svg', 'ico', 'bmp', 'avif'].includes(ext)) return 'image'
  return 'text'
}

/**
 * Read-only file viewer: docked in the right rail, or popped out over the
 * app and docked back again.
 *
 * Deliberately not an editor — the agent writes files here, and a
 * half-editor that silently discards your changes is worse than no editor.
 * Text shows as coloured code. Markdown, HTML, PDFs and images also get a
 * rendered view with a Preview/Code switch: HTML renders in a sandboxed
 * frame served by the engine (so its relative assets resolve), PDFs use
 * the built-in viewer, images just display.
 */
export function FileViewer({ file, url, onClose, onMention, popped, onPop, onDock }: {
  file: File
  /** Engine URL for the file, for frames and images. */
  url: string | null
  onClose: () => void
  onMention: (path: string) => void
  popped: boolean
  onPop: () => void
  onDock: () => void
}) {
  const name = file.path.split('/').pop() ?? file.path
  const kind = kindOf(name)
  const renderable = kind !== 'text' && !file.error
  // Rendered views are the point for these types — start there, and
  // remember the switch per kind so the choice sticks across files.
  const [modes, setModes] = useState<Partial<Record<Kind, Mode>>>(() => {
    try { return JSON.parse(localStorage.getItem('mesh.viewmodes') || '{}') } catch { return {} }
  })
  const mode: Mode = renderable ? (modes[kind] ?? 'preview') : 'code'
  const setMode = (m: Mode) => setModes((o) => {
    const next = { ...o, [kind]: m }
    try { localStorage.setItem('mesh.viewmodes', JSON.stringify(next)) } catch { /* ignore */ }
    return next
  })

  useEffect(() => {
    if (!popped) return
    const k = (e: KeyboardEvent) => { if (e.key === 'Escape') onDock() }
    window.addEventListener('keydown', k)
    return () => window.removeEventListener('keydown', k)
  }, [popped, onDock])

  const lines = useMemo(
    () => (file.content && mode === 'code' ? highlightLines(file.content, langForFile(name)) : []),
    [file.content, name, mode])

  // Binary/too-large only matter for the CODE view; a PDF or image is
  // always "binary" and still renders fine by URL.
  const codeBlocked = file.binary || file.too_large

  const panel = (
    <div className={`viewer-panel ${popped ? 'popped' : ''}`}>
      <header className="viewer-head">
        <span className="tool-chip read_file">file</span>
        <h2 title={file.path}>{name}</h2>
        <div className="viewer-actions">
        <button className="viewer-act" title="Mention in chat (@)" aria-label="Mention in chat"
                onClick={() => onMention(file.path)}>
          <svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor"
               strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
            <circle cx="8" cy="8" r="2.6" />
            <path d="M10.6 8v1a1.7 1.7 0 0 0 3.4 0V8a6 6 0 1 0-2.4 4.8" />
          </svg>
        </button>
        {popped ? (
          <button className="viewer-act" title="Dock to the side panel (esc)" aria-label="Dock to the side panel"
                  onClick={onDock}>
            <svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor"
                 strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
              <rect x="2" y="3" width="12" height="10" rx="1.6" />
              <path d="M10 3v10" /><path d="M5 6.5 7 8l-2 1.5" />
            </svg>
          </button>
        ) : (
          <button className="viewer-act" title="Open in a popup" aria-label="Open in a popup" onClick={onPop}>
            <svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor"
                 strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
              <path d="M7 3H3.6A1.6 1.6 0 0 0 2 4.6v7.8A1.6 1.6 0 0 0 3.6 14h7.8a1.6 1.6 0 0 0 1.6-1.6V9" />
              <path d="M9.5 2H14v4.5" /><path d="M14 2 7.5 8.5" />
            </svg>
          </button>
        )}
        <button className="viewer-act" title="Close file" aria-label="Close file" onClick={onClose}>
          <svg viewBox="0 0 16 16" width="13" height="13" fill="none" stroke="currentColor"
               strokeWidth="1.7" strokeLinecap="round">
            <path d="M4 4l8 8M12 4l-8 8" />
          </svg>
        </button>
        </div>
        <div className="viewer-sub">
          <span className="viewer-meta">
            {file.lines && !file.binary ? `${file.lines} lines` : ''}
            {file.size ? `${file.lines && !file.binary ? ' · ' : ''}${fmtSize(file.size)}` : ''}
          </span>
          {renderable && (
            <div className="seg small viewer-modes" role="tablist">
              <button role="tab" aria-selected={mode === 'preview'}
                      className={mode === 'preview' ? 'on' : ''}
                      onClick={() => setMode('preview')}>Preview</button>
              <button role="tab" aria-selected={mode === 'code'}
                      className={mode === 'code' ? 'on' : ''}
                      onClick={() => setMode('code')}>Code</button>
            </div>
          )}
        </div>
      </header>

      <div className={`viewer-body ${mode === 'preview' ? `is-${kind}` : ''}`}>
        {file.error && <div className="notice error">{file.error}</div>}

        {mode === 'preview' && kind === 'markdown' && (
          file.too_large
            ? <div className="notice info">File is {fmtSize(file.size ?? 0)} — too large to render.</div>
            : <div className="viewer-md md"><Markdown text={file.content} /></div>
        )}
        {mode === 'preview' && kind === 'html' && url && (
          // No allow-same-origin: the page runs as an opaque origin, so it
          // cannot reach the engine socket or this app's storage.
          <iframe className="viewer-frame" title={name} src={url}
                  sandbox="allow-scripts allow-forms allow-popups allow-modals" />
        )}
        {mode === 'preview' && kind === 'pdf' && url && (
          // Chromium's PDF plugin refuses to run in a sandboxed frame.
          <iframe className="viewer-frame" title={name} src={url} />
        )}
        {mode === 'preview' && kind === 'image' && url && (
          <div className="viewer-img"><img src={url} alt={name} /></div>
        )}
        {mode === 'preview' && !url && kind !== 'markdown' && (
          <div className="notice info">Preview unavailable — the engine did not report this file's folder.</div>
        )}

        {mode === 'code' && codeBlocked && (
          <div className="notice info">
            {file.binary
              ? 'Binary file — no code view.'
              : `File is ${fmtSize(file.size ?? 0)} — too large to show. Ask the agent to read the part you need.`}
          </div>
        )}
        {mode === 'code' && !codeBlocked && !!lines.length && (
          <pre className="viewer-code">
            {lines.map((l, i) => (
              <div className="viewer-line" key={i}>
                <span className="viewer-ln">{i + 1}</span>
                <span className="viewer-src"><Line spans={l} /></span>
              </div>
            ))}
          </pre>
        )}
      </div>

      <footer className="viewer-foot">
        <span className="viewer-path" title={file.path}>{file.path}</span>
      </footer>
    </div>
  )

  if (!popped) return panel
  return createPortal(
    <div className="modal-scrim" onClick={onDock}>
      <div className="modal viewer-pop" onClick={(e) => e.stopPropagation()}
           role="dialog" aria-modal="true">
        {panel}
      </div>
    </div>,
    document.body,
  )
}

const fmtSize = (n: number) =>
  n < 1024 ? `${n} B` : n < 1048576 ? `${(n / 1024).toFixed(1)} KB` : `${(n / 1048576).toFixed(1)} MB`
