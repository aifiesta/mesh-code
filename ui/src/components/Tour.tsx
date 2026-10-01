import { useEffect, useLayoutEffect, useRef, useState } from 'react'

/**
 * First-run walkthrough.
 *
 * Anchored to real elements rather than a slideshow of screenshots: each
 * step spotlights the actual control it describes, so what you learn is
 * where things ARE, not what they looked like in a marketing shot. A step
 * whose target is missing (no project open yet, no rail visible) is skipped
 * automatically rather than pointing at nothing.
 *
 * Shown once. `localStorage` gates it, and it can be replayed from the
 * command palette — the flag is a convenience, not a decision the user is
 * stuck with.
 */
export type TourStep = {
  id: string
  /** CSS selector for the element to spotlight. Omit for a centred card. */
  target?: string
  title: string
  body: string
  placement?: 'top' | 'bottom' | 'left' | 'right'
}

export const TOUR: TourStep[] = [
  {
    id: 'welcome',
    title: 'Welcome to Mesh Code',
    body: 'A coding agent that plans, writes files and runs commands inside a project folder you choose. Two minutes and you will know where everything is.',
  },
  {
    id: 'projects',
    target: '.sidebar',
    placement: 'right',
    title: 'Projects live here',
    body: 'Open a folder and the agent works inside it — reading, writing and running commands there and nowhere else. Each folder is its own session with its own history. Drag the right edge to resize.',
  },
  {
    id: 'composer',
    target: '.composer',
    placement: 'top',
    title: 'Ask for anything',
    body: 'Describe the outcome, not the steps: “add a health endpoint and run the tests”. Enter sends, Shift+Enter adds a line. You can keep typing while it works — messages queue.',
  },
  {
    id: 'mode',
    target: '.mode-picker',
    placement: 'top',
    title: 'You decide what it can do',
    body: 'Ask every time is the safe default. Accept edits auto-approves writes inside the project; Auto adds shell commands. Even on Bypass, dangerous shapes — ~/.ssh, rm -rf, sudo — still stop and ask.',
  },
  {
    id: 'model',
    target: '.controlbar .ctl-route',
    placement: 'top',
    title: 'Routing picks the model',
    body: 'Smart routing chooses per prompt from a bundled table — locally, in microseconds, with no extra tokens billed. Click to see which model it would choose and why, before you spend anything.',
  },
  {
    id: 'cost',
    target: '.controlbar .ctl-cost',
    placement: 'top',
    title: 'Cost and tokens, live',
    body: 'The session spend and token count, updated per hop as the agent works — not just at the end. When a model reports no price it is computed from the catalog and marked ~. The popover holds the token-savings dial for long runs.',
  },
  {
    id: 'rail',
    target: '.rail-tabs',
    placement: 'left',
    title: 'Plan, Preview, Account',
    body: 'Multi-step work gets a checklist you can watch. When the agent starts a dev server it renders right here. Account is where your key and session spend live.',
  },
  {
    id: 'palette',
    title: 'Everything is one shortcut away',
    body: '⌘K searches every setting and action by name or by what it does — try “cheap”. ⌘, opens the full settings, including a fuzzy-searchable browser of every model on your account.',
  },
]

export function Tour({ onDone }: { onDone: () => void }) {
  const [i, setI] = useState(0)
  const [rect, setRect] = useState<DOMRect | null>(null)
  const cardRef = useRef<HTMLDivElement>(null)
  // The card's real size, so it can be kept fully inside the window.
  const [size, setSize] = useState({ w: 330, h: 240 })
  useLayoutEffect(() => {
    const el = cardRef.current
    if (el) setSize({ w: el.offsetWidth, h: el.offsetHeight })
  }, [i, rect])

  // Skip steps whose anchor is not on screen — pointing at nothing is worse
  // than saying less.
  const steps = TOUR.filter((s) => !s.target || document.querySelector(s.target))
  const step = steps[i]

  useLayoutEffect(() => {
    if (!step) return
    const measure = () => {
      if (!step.target) return setRect(null)
      const el = document.querySelector(step.target)
      setRect(el ? el.getBoundingClientRect() : null)
    }
    measure()
    window.addEventListener('resize', measure)
    return () => window.removeEventListener('resize', measure)
  }, [step])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onDone()
      if (e.key === 'ArrowRight' || e.key === 'Enter') next()
      if (e.key === 'ArrowLeft') setI((n) => Math.max(0, n - 1))
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  })

  if (!step) return null
  const next = () => (i + 1 >= steps.length ? onDone() : setI(i + 1))

  const pad = 8
  const hole = rect && {
    left: rect.left - pad, top: rect.top - pad,
    width: rect.width + pad * 2, height: rect.height + pad * 2,
  }

  return (
    <div className="tour" role="dialog" aria-modal="true" aria-label="Guided tour">
      {/* Four panels around the spotlight rather than one box-shadow ring:
          the cut-out stays crisp at any radius and the dimmed area is still
          click-through-proof. */}
      {hole ? (
        <>
          <div className="tour-mask" style={{ inset: `0 0 auto 0`, height: Math.max(0, hole.top) }} />
          <div className="tour-mask" style={{ top: hole.top, left: 0, width: Math.max(0, hole.left), height: hole.height }} />
          <div className="tour-mask" style={{ top: hole.top, left: hole.left + hole.width, right: 0, height: hole.height }} />
          <div className="tour-mask" style={{ top: hole.top + hole.height, left: 0, right: 0, bottom: 0 }} />
          <div className="tour-ring" style={{ ...hole }} />
        </>
      ) : (
        <div className="tour-mask" style={{ inset: 0 }} />
      )}

      {/* Position and animation are separated on purpose: the `pop` keyframe
          ends on `transform: none`, which silently wiped the placement
          transform and dropped every card on top of the thing it was meant
          to be pointing at. The outer node positions; the inner animates. */}
      <div
        className={`tour-pos ${step.target && hole ? 'anchored' : 'center'}`}
        style={step.target && hole ? cardPos(step.placement, hole, size) : undefined}
      >
      <div ref={cardRef} className="tour-card">
        <div className="tour-progress">
          {steps.map((_, n) => <span key={n} className={n === i ? 'on' : n < i ? 'done' : ''} />)}
        </div>
        <h3>{step.title}</h3>
        <p>{step.body}</p>
        <div className="tour-actions">
          <button className="btn ghost" onClick={onDone}>Skip tour</button>
          <div className="spacer" />
          {i > 0 && <button className="btn subtle" onClick={() => setI(i - 1)}>Back</button>}
          <button className="btn primary" onClick={next}>
            {i + 1 >= steps.length ? 'Start building' : 'Next'}
          </button>
        </div>
      </div>
      </div>
    </div>
  )
}

function cardPos(
  p: string | undefined,
  h: { left: number; top: number; width: number; height: number },
  size: { w: number; h: number },
) {
  const gap = 14, M = 16
  const vw = window.innerWidth, vh = window.innerHeight
  const W = size.w, H = size.h
  // Flip to the opposite side when the asked-for side has no room.
  let side = p ?? 'bottom'
  if (side === 'top' && h.top - gap - H < M) side = 'bottom'
  else if (side === 'bottom' && h.top + h.height + gap + H > vh - M) side = 'top'
  else if (side === 'left' && h.left - gap - W < M) side = 'right'
  else if (side === 'right' && h.left + h.width + gap + W > vw - M) side = 'left'

  let left: number, top: number
  switch (side) {
    case 'top':    left = h.left + h.width / 2 - W / 2; top = h.top - gap - H; break
    case 'left':   left = h.left - gap - W; top = h.top + h.height / 2 - H / 2; break
    case 'right':  left = h.left + h.width + gap; top = h.top + h.height / 2 - H / 2; break
    default:       left = h.left + h.width / 2 - W / 2; top = h.top + h.height + gap
  }
  // Whatever side won, the card stays inside the window with a margin.
  left = Math.min(Math.max(left, M), Math.max(M, vw - M - W))
  top = Math.min(Math.max(top, M), Math.max(M, vh - M - H))
  return { left, top }
}
