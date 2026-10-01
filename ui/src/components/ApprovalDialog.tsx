import { useEffect, useRef } from 'react'
import type { Approval } from '../types'
import { Diff } from './Diff'

/**
 * The GUI answer to the CLI's `y/n` prompt.
 *
 * Two things this must get right. First, it shows what will actually
 * happen — a diff for a write, the verbatim command for a shell call —
 * because "Allow write_file?" is not a decision anyone can make. Second,
 * `blockedReason` is surfaced prominently: when it is set, the user has an
 * auto-approve mode ON and a safety gate pulled this call back to a
 * question anyway. That is the most security-relevant moment in the app.
 */
export function ApprovalDialog({ approval, onDecide, isTop = true }: {
  approval: Approval
  onDecide: (d: 'allow' | 'deny' | 'always') => void
  /** True when no palette/sheet/menu is layered above this dialog. Escape
   *  and ⌘⏎ only act then, so a keystroke meant for another overlay can't
   *  deny or approve the pending tool call. */
  isTop?: boolean
}) {
  const allowRef = useRef<HTMLButtonElement>(null)

  useEffect(() => { allowRef.current?.focus() }, [approval.token])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      // Escape denies a tool — a destructive action. Only honour it when
      // this dialog is the topmost overlay; otherwise an Escape aimed at a
      // palette/menu opened over it would silently deny the pending call.
      if (e.key === 'Escape' && isTop) { e.stopPropagation(); onDecide('deny') }
      if (e.key === 'Enter' && (e.metaKey || e.ctrlKey) && isTop) onDecide('allow')
    }
    window.addEventListener('keydown', onKey, true)
    return () => window.removeEventListener('keydown', onKey, true)
  }, [onDecide, isTop])

  const p = approval.preview ?? {}

  return (
    <div className="modal-scrim">
      <div className="modal approval" role="dialog" aria-modal="true">
        <header>
          <span className={`tool-chip ${approval.name}`}>{approval.name.replace('_', ' ')}</span>
          <h2>{titleFor(approval.name)}</h2>
        </header>

        {approval.blockedReason && (
          <div className="safety-flag">
            <strong>Held back by a safety check</strong>
            <p>
              You have auto-approval on, but this call was stopped anyway:{' '}
              <em>{approval.blockedReason}</em>
            </p>
          </div>
        )}

        <div className="approval-body">
          {p.kind === 'diff' && (
            <>
              <div className="path-line" title={p.path}>{p.path}</div>
              <Diff oldText={p.old ?? ''} newText={p.new ?? ''} exists={!!p.exists} />
            </>
          )}
          {p.kind === 'command' && (
            <>
              <div className="path-line">in {p.cwd}</div>
              <pre className="command-block">{p.command}</pre>
            </>
          )}
          {p.kind === 'path' && <pre className="command-block">{p.path}</pre>}
          {p.kind === 'query' && <pre className="command-block">{p.query}</pre>}
          {p.kind === 'json' && <pre className="command-block">{JSON.stringify(p.value, null, 2)}</pre>}
        </div>

        <footer>
          <button className="btn ghost" onClick={() => onDecide('deny')}>
            Reject <kbd>esc</kbd>
          </button>
          <div className="spacer" />
          <button className="btn subtle" onClick={() => onDecide('always')}>
            Always allow {approval.name.replace('_', ' ')}
          </button>
          <button ref={allowRef} className="btn primary" onClick={() => onDecide('allow')}>
            Approve <kbd>⌘⏎</kbd>
          </button>
        </footer>
      </div>
    </div>
  )
}

function titleFor(name: string) {
  switch (name) {
    case 'write_file': return 'Write this file?'
    case 'run_bash': return 'Run this command?'
    case 'start_server': return 'Start this server?'
    case 'read_file': return 'Read this file?'
    case 'web_search': return 'Search the web?'
    default: return `Run ${name}?`
  }
}
