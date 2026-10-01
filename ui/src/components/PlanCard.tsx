import type { Plan } from '../types'

const MARK: Record<string, string> = {
  completed: '✓', in_progress: '', blocked: '✕', pending: '',
}

/**
 * The plan, inline in the transcript. The CLI printed this checklist into
 * the scrollback whenever it changed; here one card updates in place, so
 * progress is visible in the flow of the conversation without a new
 * checklist appearing on every single step transition.
 */
export function PlanCard({ plan }: { plan: Plan }) {
  const pct = plan.total ? (plan.done / plan.total) * 100 : 0
  return (
    <div className={`plan-card ${plan.complete ? 'done' : ''}`}>
      <div className="plan-card-head">
        <span className="plan-title">Plan</span>
        <span className="plan-count">{plan.done} of {plan.total}</span>
        <div className="plan-track"><div className="plan-fill" style={{ width: `${pct}%` }} /></div>
      </div>
      <ol className="plan-steps">
        {plan.steps.map((s, i) => (
          <li key={s.index} className={s.status} style={{ animationDelay: `${i * 28}ms` }}>
            <span className="step-mark">
              {s.status === 'in_progress'
                ? <span className="step-spin" aria-hidden />
                : (MARK[s.status] || <span className="step-dot" aria-hidden />)}
            </span>
            <span className="step-title">{s.title}</span>
          </li>
        ))}
      </ol>
    </div>
  )
}
