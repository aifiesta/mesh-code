import type { HelperRun } from '../types'

const LIVE = new Set(['starting', 'working'])

/**
 * Deep work helpers, inline in the transcript: how many are working, and
 * each one's piece, status and cost. One card per hand-off, updated in place.
 */
export function HelpersCard({ run, money }: {
  run: HelperRun
  money: (usd: number, usdFmt: (n: number) => string, estimated?: boolean) => string
}) {
  const n = run.helpers.length
  const fmt = (v: number) => '$' + v.toFixed(4)
  const head = run.working > 0
    ? `${run.working} helper${run.working === 1 ? '' : 's'} working`
    : `${n} helper${n === 1 ? '' : 's'} finished`
  const model = run.model.split('/').pop() ?? run.model
  return (
    <div className={`plan-card helpers-card ${run.working ? '' : 'done'}`}>
      <div className="plan-card-head">
        <span className="plan-title">{head}</span>
        <span className="plan-count">{model} · {money(run.cost, fmt)}</span>
      </div>
      <ol className="plan-steps">
        {run.helpers.map((h) => (
          <li key={h.n} className={LIVE.has(h.status) ? 'in_progress' : h.status === 'done' ? 'completed' : 'blocked'}>
            <span className="step-mark">
              {LIVE.has(h.status) ? <span className="step-spin" aria-hidden />
                : h.status === 'done' ? '✓' : '✕'}
            </span>
            <span className="step-title" title={h.files.join(', ')}>
              {h.title}
              <span className="helper-meta">
                {' · '}{h.status}{' · '}{h.steps} step{h.steps === 1 ? '' : 's'}
                {' · '}{money(h.cost, fmt, h.estimated)}
              </span>
            </span>
          </li>
        ))}
      </ol>
    </div>
  )
}
