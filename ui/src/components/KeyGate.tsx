import { useEffect, useState } from 'react'
import { Wordmark } from './Logo'

/**
 * Connect, replace, or remove the Mesh API key.
 *
 * One component, two placements. As a `gate` it is the first-run screen; as
 * a `modal` it is how you change keys later — which had no route at all
 * before: the gate only ever appeared when NO key was stored, so anyone
 * whose key was picked up from the meshapi CLI had no way to set their own.
 *
 * The key is verified against the gateway before it is saved, so a typo
 * fails here rather than on the first prompt.
 */
export function KeyGate({ variant = 'gate', error, resultAt, hint, source, onSave, onClear, onClose }: {
  variant?: 'gate' | 'modal'
  error: string | null
  /** Bumped whenever ANY key.result lands — success or failure. */
  resultAt?: number
  hint?: string
  source?: string
  onSave: (key: string) => void
  onClear?: () => void
  onClose?: () => void
}) {
  const [key, setKey] = useState('')
  const [sent, setSent] = useState(false)

  // Any result clears the pending state. Watching only `error` meant a
  // SUCCESS left the button reading "Verifying…" forever in modal mode —
  // the full-screen gate got away with it only because it unmounts.
  useEffect(() => { setSent(false) }, [error, resultAt])

  // And bound the wait regardless: if the engine never answers — dropped
  // socket, killed process — the button must still come back.
  useEffect(() => {
    if (!sent) return
    const t = setTimeout(() => setSent(false), 25000)
    return () => clearTimeout(t)
  }, [sent])
  useEffect(() => {
    if (variant !== 'modal' || !onClose) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [variant, onClose])

  const submit = () => { if (key.trim()) { onSave(key.trim()); setSent(true) } }

  const card = (
    <div className="gate-card">
      {variant === 'gate' ? (
        <div className="brand">
          <Wordmark className="gate-word" />
          <div className="gate-sub">Code</div>
        </div>
      ) : (
        <h2 className="gate-title">API key</h2>
      )}

      <p className="lede">
        {variant === 'gate'
          ? 'Connect your Mesh API key to get started. One key, a thousand models.'
          : hint
            ? <>Currently using <code>{hint}</code>{source && <> from {source}</>}. Paste a new key to replace it.</>
            : 'Paste a Mesh API key to connect.'}
      </p>

      <label>
        {variant === 'gate' ? 'API key' : 'New key'}
        <input
          type="password"
          placeholder="rsk_…"
          value={key}
          autoFocus
          onChange={(e) => { setKey(e.target.value); setSent(false) }}
          onKeyDown={(e) => { if (e.key === 'Enter') submit() }}
        />
      </label>

      {error && !sent && <div className="gate-error">{error}</div>}

      <div className="gate-actions">
        {variant === 'modal' && (
          <button className="btn ghost" onClick={onClose}>Cancel <kbd>esc</kbd></button>
        )}
        <div className="spacer" />
        {variant === 'modal' && onClear && (
          <button className="btn ghost danger" onClick={onClear}>Sign out</button>
        )}
        <button className="btn primary" disabled={!key.trim() || sent} onClick={submit}>
          {sent && !error ? 'Verifying…' : variant === 'gate' ? 'Connect' : 'Save key'}
        </button>
      </div>

      <p className="fine">
        Get a key at <a href="https://app.meshapi.ai" target="_blank" rel="noreferrer noopener">app.meshapi.ai</a>.
        It is stored locally with owner-only permissions and is only ever sent
        to the Mesh gateway. Already using the meshapi CLI? Its key is picked
        up automatically.
      </p>
    </div>
  )

  if (variant === 'modal') {
    return <div className="modal-scrim" onClick={onClose}>
      <div onClick={(e) => e.stopPropagation()}>{card}</div>
    </div>
  }
  return <div className="gate">{card}</div>
}
