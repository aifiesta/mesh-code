import { useEffect, useMemo, useRef, useState } from 'react'
import { MODES, type Mode } from '../types'
import { useDismissable } from '../useDismissable'
import { fuzzyMatch } from '../fuzzy'
import { Highlight } from './Highlight'
import { IconSend, IconX } from './Chevron'
import { FileBadge } from './FileBadge'

/** MIME type the explorer puts on a dragged file row. */
export const DRAG_PATH = 'application/x-mesh-path'

/** `@query` being typed at the caret, or null. */
function atQuery(text: string, caret: number): { start: number; q: string } | null {
  const before = text.slice(0, caret)
  const m = before.match(/(?:^|\s)@([^\s@]*)$/)
  if (!m) return null
  return { start: before.length - m[1].length - 1, q: m[1] }
}

/**
 * The input. Enter sends, Shift+Enter makes a newline, and the box stays
 * live while the model works — a queued message runs as soon as the current
 * turn ends, which is the CLI's type-ahead behaviour carried over.
 * Images can be pasted or dropped and ride along as multimodal content.
 */
/** Longest side a pasted screenshot is sent at; above this a model sees no more detail. */
const MAX_IMAGE_SIDE = 1600
const MAX_IMAGE_BYTES = 350_000

/** Re-encode an image data URL as a bounded JPEG. Returns the input when it is already small. */
async function shrinkImage(dataUrl: string, mime: string): Promise<string> {
  if (mime === 'image/gif') return dataUrl
  if (dataUrl.length <= MAX_IMAGE_BYTES * 1.37) {
    const probe = await loadImage(dataUrl).catch(() => null)
    if (!probe || Math.max(probe.width, probe.height) <= MAX_IMAGE_SIDE) return dataUrl
  }
  const img = await loadImage(dataUrl).catch(() => null)
  if (!img) return dataUrl
  const scale = Math.min(1, MAX_IMAGE_SIDE / Math.max(img.width, img.height))
  const canvas = document.createElement('canvas')
  canvas.width = Math.round(img.width * scale)
  canvas.height = Math.round(img.height * scale)
  const ctx = canvas.getContext('2d')
  if (!ctx) return dataUrl
  ctx.fillStyle = '#fff'
  ctx.fillRect(0, 0, canvas.width, canvas.height)
  ctx.drawImage(img, 0, 0, canvas.width, canvas.height)
  let q = 0.85
  let out = canvas.toDataURL('image/jpeg', q)
  while (out.length > MAX_IMAGE_BYTES * 1.37 && q > 0.5) {
    q -= 0.1
    out = canvas.toDataURL('image/jpeg', q)
  }
  return out.length < dataUrl.length ? out : dataUrl
}

function loadImage(src: string): Promise<HTMLImageElement> {
  return new Promise((res, rej) => {
    const img = new Image()
    img.onload = () => res(img)
    img.onerror = rej
    img.src = src
  })
}

export function Composer({ busy, stopping, mode, controls, onSend, onInterrupt, onMode, onCommands,
                          fileIndex, onNeedFiles }: {
  busy: boolean
  /** Stop was clicked; the engine has not confirmed the abort yet. */
  stopping?: boolean
  mode: Mode
  /** Model / routing / cost, rendered in the quiet row below the box. */
  controls?: React.ReactNode
  onSend: (text: string, attachments: { name: string; data_url: string }[], refs: string[]) => void
  onInterrupt: () => void
  onMode: (m: Mode) => void
  onCommands: () => void
  /** Workspace file list for the @ picker; null until fetched. */
  fileIndex: { files: string[]; truncated: boolean } | null
  onNeedFiles: () => void
}) {
  const [text, setText] = useState('')
  const [files, setFiles] = useState<{ name: string; data_url: string }[]>([])
  /** Workspace files referenced in this message — badges above the box. */
  const [refs, setRefs] = useState<string[]>([])
  const [modeOpen, setModeOpen] = useState(false)
  const [caret, setCaret] = useState(0)
  const [pick, setPick] = useState(0)
  const [dropping, setDropping] = useState(false)
  const ref = useRef<HTMLTextAreaElement>(null)
  const modeRef = useRef<HTMLDivElement>(null)
  const fileRef = useRef<HTMLInputElement>(null)

  useDismissable(modeOpen, () => setModeOpen(false), modeRef)

  const addRef = (path: string) =>
    setRefs((r) => (r.includes(path) ? r : [...r, path]))

  // The explorer drops a path in here rather than opening an editor —
  // pointing the agent at a file is the thing you actually want to do next.
  // It lands as a badge, not as text: a path pasted mid-sentence was easy
  // to mangle and the model could not tell it from prose.
  useEffect(() => {
    const onMention = (e: Event) => {
      addRef((e as CustomEvent).detail as string)
      ref.current?.focus()
    }
    window.addEventListener('mesh:mention', onMention)
    return () => window.removeEventListener('mesh:mention', onMention)
  }, [])

  // ---- @ picker -------------------------------------------------------
  const at = useMemo(() => atQuery(text, caret), [text, caret])
  useEffect(() => {
    if (at && !fileIndex) onNeedFiles()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [!!at, !!fileIndex])
  const hits = useMemo(() => {
    if (!at || !fileIndex) return []
    const scored: { path: string; rank: number; positions: number[] }[] = []
    for (const path of fileIndex.files) {
      // Match the file NAME first (what people type), then the whole path.
      const name = path.slice(path.lastIndexOf('/') + 1)
      const byName = fuzzyMatch(at.q, name)
      const m = byName
        ? { rank: byName.rank, positions: byName.positions.map((i) => i + path.length - name.length) }
        : fuzzyMatch(at.q, path)
      if (m) scored.push({ path, rank: m.rank + (byName ? 0 : 3), positions: m.positions })
      if (scored.length > 400) break
    }
    scored.sort((a, b) => a.rank - b.rank || a.path.length - b.path.length)
    return scored.slice(0, 8)
  }, [at, fileIndex])
  useEffect(() => { setPick(0) }, [at?.q])

  function choose(path: string) {
    if (!at) return
    // Replace the typed `@query` with the badge; the text keeps flowing.
    const before = text.slice(0, at.start).replace(/\s+$/, '')
    const after = text.slice(caret).replace(/^\s+/, '')
    const next = before && after ? `${before} ${after}` : before || after
    setText(next)
    addRef(path)
    const pos = before ? before.length + (after ? 1 : 0) : 0
    requestAnimationFrame(() => {
      const el = ref.current
      if (!el) return
      el.focus(); el.setSelectionRange(pos, pos); setCaret(pos)
    })
  }

  useEffect(() => {
    const el = ref.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${Math.min(el.scrollHeight, 220)}px`
  }, [text])

  function send() {
    const t = text.trim()
    if (!t && files.length === 0 && refs.length === 0) return
    onSend(t, files, refs)
    setText('')
    setFiles([])
    setRefs([])
  }

  async function absorb(list: FileList | null) {
    if (!list) return
    for (const f of Array.from(list)) {
      if (!f.type.startsWith('image/')) continue
      const raw: string = await new Promise((res) => {
        const r = new FileReader()
        r.onload = () => res(String(r.result))
        r.readAsDataURL(f)
      })
      const data_url = await shrinkImage(raw, f.type)
      setFiles((p) => [...p, { name: f.name, data_url }])
    }
  }

  const current = MODES.find((m) => m.id === mode) ?? MODES[0]

  return (
    <div
      className={`composer ${dropping ? 'dropping' : ''}`}
      onDragOver={(e) => { e.preventDefault(); if (!dropping) setDropping(true) }}
      onDragLeave={(e) => {
        if (!e.currentTarget.contains(e.relatedTarget as Node)) setDropping(false)
      }}
      onDrop={(e) => {
        e.preventDefault()
        setDropping(false)
        // A row dragged out of the explorer carries its workspace path;
        // anything else is an image from the desktop.
        const path = e.dataTransfer.getData(DRAG_PATH)
        if (path) { addRef(path); ref.current?.focus(); return }
        absorb(e.dataTransfer.files)
      }}
    >
      {(files.length > 0 || refs.length > 0) && (
        <div className="attachments">
          {refs.map((p) => (
            <FileBadge key={p} path={p}
                       onRemove={() => setRefs((r) => r.filter((x) => x !== p))} />
          ))}
          {files.map((f, i) => (
            <span key={i} className="chip">
              {f.name}
              <button onClick={() => setFiles((p) => p.filter((_, j) => j !== i))}><IconX size={11} /></button>
            </span>
          ))}
        </div>
      )}

      {at && (
        <div className="at-menu" role="listbox">
          {hits.map((h, i) => (
            <button key={h.path} role="option" aria-selected={i === pick}
                    className={i === pick ? 'on' : ''}
                    onMouseDown={(e) => { e.preventDefault(); choose(h.path) }}
                    onMouseEnter={() => setPick(i)}>
              <span className="at-name"><Highlight text={h.path} positions={h.positions} /></span>
            </button>
          ))}
          {!fileIndex && <div className="at-empty">Indexing files…</div>}
          {fileIndex && !hits.length && <div className="at-empty">No file matches “{at.q}”</div>}
          {fileIndex?.truncated && <div className="at-empty">Large project — showing the first files only.</div>}
        </div>
      )}

      {/* The box is the box: border, focus ring and the send affordance all
          belong to the field itself. Everything else moved OUT of it and
          below, so a narrow window squeezes a quiet meta row rather than
          wrapping "Ask every time" onto three lines inside the input. */}
      <div className="composer-input">
        <textarea
          ref={ref}
          value={text}
          rows={1}
          placeholder={busy ? 'Queue another message…' : 'Ask for anything, @ a file, or / for commands'}
          onChange={(e) => { setText(e.target.value); setCaret(e.target.selectionStart ?? 0) }}
          onSelect={(e) => setCaret((e.target as HTMLTextAreaElement).selectionStart ?? 0)}
          onPaste={(e) => absorb(e.clipboardData.files)}
          onKeyDown={(e) => {
            // The @ picker owns the arrows and Enter/Tab while it is open.
            if (at && hits.length) {
              if (e.key === 'ArrowDown') { e.preventDefault(); setPick((p) => (p + 1) % hits.length); return }
              if (e.key === 'ArrowUp') { e.preventDefault(); setPick((p) => (p - 1 + hits.length) % hits.length); return }
              if (e.key === 'Enter' || e.key === 'Tab') { e.preventDefault(); choose(hits[pick].path); return }
              if (e.key === 'Escape') { e.preventDefault(); setText((t) => t.slice(0, at.start) + t.slice(caret)); return }
            }
            if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send() }
            // "/" on an empty field opens the palette. This replaces the two
            // labelled buttons that used to sit in the bar — one fewer thing
            // on screen, and the placeholder teaches it.
            if (e.key === '/' && !text) { e.preventDefault(); onCommands() }
            // Escape from the composer interrupts the run — matching the
            // Stop button's advertised shortcut. Only when focus is here, so
            // it never competes with an overlay's own Escape.
            if (e.key === 'Escape' && busy) { e.preventDefault(); onInterrupt() }
          }}
        />
        {busy ? (
          <button className={`send-btn stop ${stopping ? 'stopping' : ''}`}
                  onClick={onInterrupt} disabled={!!stopping}
                  title={stopping ? 'Stopping…' : 'Stop (esc)'}
                  aria-label={stopping ? 'Stopping' : 'Stop'}>
            <span className="stop-glyph" />
          </button>
        ) : (
          <button
            className="send-btn"
            onClick={send}
            disabled={!text.trim() && !files.length && !refs.length}
            title="Send (⏎)"
          ><IconSend size={14} /></button>
        )}
      </div>

      <div className="composer-meta">
        <div className={`mode-picker ${modeOpen ? 'open' : ''}`} ref={modeRef}>
          <button className={`mode-btn ${mode}`} onClick={() => setModeOpen((o) => !o)}>
            <span className="dot" /> {current.label}
          </button>
          {modeOpen && (
            <div className="mode-menu">
              {MODES.map((m) => (
                <button
                  key={m.id}
                  className={m.id === mode ? 'on' : ''}
                  onClick={() => { onMode(m.id); setModeOpen(false) }}
                >
                  <span className={`dot ${m.id}`} />
                  <span>
                    <strong>{m.label}</strong>
                    <em>{m.blurb}</em>
                  </span>
                </button>
              ))}
            </div>
          )}
        </div>

        {/* Drawn, not typed: a "+" glyph renders at the cap height of a
            13px font, which is about 7px of actual mark — invisible next
            to a text label. This is a 16px stroke in a 30px target. */}
        <button className="meta-icon" title="Attach images"
                onClick={() => fileRef.current?.click()} aria-label="Attach images">
          <svg viewBox="0 0 16 16" aria-hidden="true">
            <path d="M8 3.2v9.6M3.2 8h9.6" />
          </svg>
        </button>
        <input ref={fileRef} type="file" accept="image/*" multiple hidden
               onChange={(e) => { absorb(e.target.files); e.target.value = '' }} />

        <div className="spacer" />

        {controls}
      </div>
    </div>
  )
}
