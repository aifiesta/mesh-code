import { useState } from 'react'
import { createPortal } from 'react-dom'

/**
 * The beta card's pop-up: five stars and two optional boxes, submitted
 * through the engine's /api/feedback relay so nobody leaves the app.
 * Mirrors the Google Form exactly — the rating is the only required
 * field there too, so a tap and Send is a complete response.
 *
 * Rendered through a portal: the sidebar it is triggered from sits under
 * ancestors whose transforms would turn `position: fixed` into
 * sidebar-relative and pin the scrim to a 200px column. From body, the
 * scrim covers the window and the dialog centers like every other modal.
 */
export function FeedbackDialog({ onSubmit, onClose }: {
  onSubmit: (rating: number, good: string, bad: string) => Promise<boolean>
  onClose: () => void
}) {
  const [rating, setRating] = useState(0)
  const [hover, setHover] = useState(0)
  const [good, setGood] = useState('')
  const [bad, setBad] = useState('')
  const [phase, setPhase] = useState<'edit' | 'sending' | 'done' | 'failed'>('edit')

  const send = async () => {
    if (!rating || phase === 'sending') return
    setPhase('sending')
    const ok = await onSubmit(rating, good, bad)
    if (ok) {
      setPhase('done')
      setTimeout(onClose, 1400)
    } else {
      // Text stays in the boxes — a failed send must never eat what
      // someone bothered to type.
      setPhase('failed')
    }
  }

  return createPortal(
    <div className="modal-scrim" onClick={onClose}>
      <div className="modal feedback" onClick={(e) => e.stopPropagation()}>
        <header>
          <h2>How's Mesh Code so far?</h2>
        </header>

        {phase === 'done' ? (
          <div className="feedback-done">
            <strong>Thank you!</strong>
            <em>Every answer shapes what we fix next.</em>
          </div>
        ) : (
          <>
            <div className="feedback-body">
              <div className="stars" role="radiogroup" aria-label="Rating out of 5">
                {[1, 2, 3, 4, 5].map((n) => (
                  <button
                    key={n}
                    className={(hover || rating) >= n ? 'on' : ''}
                    role="radio"
                    aria-checked={rating === n}
                    aria-label={`${n} star${n > 1 ? 's' : ''}`}
                    onMouseEnter={() => setHover(n)}
                    onMouseLeave={() => setHover(0)}
                    onClick={() => setRating(n)}
                  >
                    <svg viewBox="0 0 24 24" aria-hidden>
                      <path d="M12 2.6l2.9 5.9 6.5.9-4.7 4.6 1.1 6.4L12 17.4l-5.8 3 1.1-6.4L2.6 9.4l6.5-.9z" />
                    </svg>
                  </button>
                ))}
              </div>
              <div className="field">
                <textarea rows={2} placeholder="What's working well? (optional)"
                          value={good} onChange={(e) => setGood(e.target.value)} />
              </div>
              <div className="field">
                <textarea rows={2} placeholder="What's broken, annoying, or missing? (optional)"
                          value={bad} onChange={(e) => setBad(e.target.value)} />
              </div>
              {phase === 'failed' && (
                <p className="feedback-error">
                  Couldn't send — check your connection and try again.
                </p>
              )}
            </div>
            <footer>
              <span className="feedback-hint">Goes straight to the team.</span>
              <button className="btn" onClick={onClose}>Cancel</button>
              <button className="btn primary" disabled={!rating || phase === 'sending'}
                      onClick={send}>
                {phase === 'sending' ? 'Sending…' : 'Send feedback'}
              </button>
            </footer>
          </>
        )}
      </div>
    </div>,
    document.body,
  )
}
