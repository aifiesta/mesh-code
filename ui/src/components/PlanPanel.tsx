import type { Plan } from '../types'

const MARK: Record<string, string> = {
  completed: '✓', in_progress: '▸', blocked: '✕', pending: '○',
}

export function PlanPanel({ plan }: { plan: Plan }) {
  const pct = plan.total ? Math.round((plan.done / plan.total) * 100) : 0
  return (
    <section className="panel">
      <div className="panel-head">
        <h3>Plan</h3>
        <span className="panel-meta">{plan.done}/{plan.total}</span>
      </div>
      <div className="progress"><div className="bar" style={{ width: `${pct}%` }} /></div>
      <ol className="plan-list">
        {plan.steps.map((s) => (
          <li key={s.index} className={s.status}>
            <span className="mark">{MARK[s.status] ?? '○'}</span>
            <span className="title">{s.title}</span>
          </li>
        ))}
      </ol>
    </section>
  )
}
