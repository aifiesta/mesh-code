import { useEffect, useState } from 'react'
import { Wordmark } from './Logo'

/**
 * Boot screen, shown until the engine says hello.
 *
 * It exists for correctness as much as polish: until `engine.ready` lands
 * we do not know whether a key is stored, so rendering anything decisive
 * would mean flashing a login screen at someone who is already signed in.
 *
 * The status line only escalates if the wait is genuinely long — a fast
 * start should not flicker three messages on the way past.
 */
const STAGES = [
  { after: 0, text: 'Starting the engine…' },
  { after: 2500, text: 'Connecting…' },
  { after: 7000, text: 'Still starting — the first launch takes a moment.' },
  { after: 16000, text: "The engine isn't responding. Retrying…" },
]

export function Splash({ connection }: { connection: 'connecting' | 'open' | 'closed' }) {
  const [waited, setWaited] = useState(0)

  useEffect(() => {
    const t0 = Date.now()
    const id = setInterval(() => setWaited(Date.now() - t0), 500)
    return () => clearInterval(id)
  }, [])

  const stage = [...STAGES].reverse().find((s) => waited >= s.after) ?? STAGES[0]
  const stalled = waited > 16000 || connection === 'closed'

  return (
    <div className="splash">
      <div className="splash-inner">
        <Wordmark className="splash-word" />
        <span className="splash-product">Code</span>
        <div className="splash-rule"><span /></div>
        <p className={`splash-status ${stalled ? 'stalled' : ''}`}>{stage.text}</p>
      </div>
    </div>
  )
}
