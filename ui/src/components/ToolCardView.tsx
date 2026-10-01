import { useState } from 'react'
import type { ToolCard } from '../types'
import { Caret } from './Chevron'

const ICON: Record<string, string> = {
  read_file: '◇', write_file: '◆', run_bash: '›_', web_search: '⌕',
  start_server: '▶', remember: '✦', create_plan: '☰', update_step: '☑',
}

/** One tool call in the transcript: what ran, and what came back. */
export function ToolCardView({ card }: { card: ToolCard }) {
  const [open, setOpen] = useState(false)
  const hasBody = !!card.result || !!card.reason

  return (
    <div className={`tool-card ${card.status}`}>
      <button className="tool-head" onClick={() => hasBody && setOpen((o) => !o)}>
        <span className="tool-icon">{ICON[card.name] ?? '⚙'}</span>
        <span className="tool-summary">{card.summary}</span>
        <span className="tool-state">
          {card.status === 'running' && <span className="pulse">running…</span>}
          {card.status === 'ok' && (card.deduped ? 'cached' : 'done')}
          {card.status === 'error' && 'failed'}
          {card.status === 'denied' && 'rejected'}
          {card.status === 'skipped' && 'skipped'}
        </span>
        {hasBody && <span className="tool-caret"><Caret open={open} /></span>}
      </button>
      {open && hasBody && (
        <pre className="tool-body">{card.reason ?? card.result}</pre>
      )}
    </div>
  )
}
