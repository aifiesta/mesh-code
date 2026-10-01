import { useEffect, useState } from 'react'
import type { Plan } from '../types'

/**
 * The "it is working" indicator.
 *
 * The CLI had a phase-aware spinner that named what was happening
 * (`preparing write_file (↓ 3.2k chars)`), and that specificity is the
 * whole value — a generic spinner tells you nothing about whether to wait
 * or intervene. So this shows, in order of preference: the tool whose
 * arguments are streaming, the plan step in progress, or the elapsed time.
 */
export function Thinking({ progress, plan, hop }: {
  progress: { tool: string | null; chars: number } | null
  plan: Plan | null
  hop: number
}) {
  const [secs, setSecs] = useState(0)
  useEffect(() => {
    const t0 = Date.now()
    const id = setInterval(() => setSecs(Math.floor((Date.now() - t0) / 1000)), 500)
    return () => clearInterval(id)
  }, [])

  const step = plan?.steps.find((s) => s.status === 'in_progress')

  let label = 'Thinking'
  if (progress?.tool) {
    label = `Preparing ${progress.tool.replace('_', ' ')}`
    if (progress.chars > 400) label += ` · ${(progress.chars / 1000).toFixed(1)}k chars`
  } else if (step) {
    label = step.title
  }

  return (
    <div className="thinking">
      <span className="orbit" aria-hidden>
        <i /><i /><i />
      </span>
      <span className="thinking-label">{label}</span>
      <span className="thinking-meta">
        {hop > 1 && <>step {hop} · </>}{secs}s
      </span>
    </div>
  )
}
