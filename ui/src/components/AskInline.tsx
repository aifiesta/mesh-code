import { useState } from 'react'
import type { Ask } from '../types'

/**
 * The model's mid-task question, inline in the transcript.
 *
 * It used to be a modal. A modal is wrong here for two reasons: it hides the
 * conversation you need in order to answer, and once answered it vanishes —
 * so the transcript has no record that the question was ever asked or what
 * you said. Inline, the exchange stays part of the history.
 *
 * Answered questions collapse to a one-line summary rather than disappearing.
 */
export function AskInline({ ask, answered, onAnswer, onDismiss }: {
  ask: Ask
  answered?: string[]
  onAnswer: (answers: string[]) => void
  onDismiss: () => void
}) {
  const [picked, setPicked] = useState<Record<number, string>>({})
  const [other, setOther] = useState<Record<number, string>>({})

  if (answered) {
    return (
      <div className="ask-inline done">
        <span className="ask-inline-mark">✓</span>
        <div>
          {ask.questions.map((q, i) => (
            <div className="ask-answered" key={i}>
              <span className="ask-answered-q">{q.question}</span>
              <span className="ask-answered-a">{answered[i]}</span>
            </div>
          ))}
        </div>
      </div>
    )
  }

  const answers = ask.questions.map((_, i) => other[i]?.trim() || picked[i] || '')
  const ready = answers.every((a) => a.length > 0)

  return (
    <div className="ask-inline">
      <div className="ask-inline-head">
        <span className="ask-inline-mark pending">?</span>
        <span>{ask.questions.length > 1 ? 'A few questions' : 'One question'}</span>
      </div>

      {ask.questions.map((q, i) => (
        <div className="ask-q" key={i}>
          {q.header && <div className="ask-header">{q.header}</div>}
          <div className="ask-text">{q.question}</div>
          <div className="ask-options">
            {(q.options ?? []).map((o) => (
              <button
                key={o.label}
                className={`ask-option ${picked[i] === o.label && !other[i] ? 'on' : ''}`}
                onClick={() => {
                  setPicked((p) => ({ ...p, [i]: o.label }))
                  setOther((p) => ({ ...p, [i]: '' }))
                }}
              >
                <span className="ask-option-label">{o.label}</span>
                {o.description && <span className="ask-option-desc">{o.description}</span>}
              </button>
            ))}
          </div>
          <input
            className="ask-other"
            placeholder="Something else…"
            value={other[i] ?? ''}
            onChange={(e) => setOther((p) => ({ ...p, [i]: e.target.value }))}
            onKeyDown={(e) => { if (e.key === 'Enter' && ready) onAnswer(answers) }}
          />
        </div>
      ))}

      <div className="ask-inline-actions">
        <button className="btn ghost" onClick={onDismiss}>Let the model decide</button>
        <div className="spacer" />
        <button className="btn primary" disabled={!ready} onClick={() => onAnswer(answers)}>
          Send answer
        </button>
      </div>
    </div>
  )
}
