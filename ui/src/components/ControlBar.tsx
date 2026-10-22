import { useRef, useState } from 'react'
import { useDismissable } from '../useDismissable'
import type { BudgetSnapshot, LocalStatus, SettingSpec, SettingsDoc, Status } from '../types'
import { fmtTokens, fmtUsd, pricePerM, type CatalogModel } from '../types'
import { Field } from './Field'
import { canAllowMoreToday, ringTone } from '../budget'

/**
 * The dials you touch often, where you are already looking.
 *
 * Layer one of three. The rule for what earns a pill: you change it within a
 * session, and its current value changes how you read what is on screen.
 * Model, routing and cost qualify. Stall policy and system prompt do not —
 * those live in the sheet.
 *
 * Every popover ends with the same escape hatch to the full settings, so the
 * bar teaches that more exists without having to show it.
 */
export function ControlBar({ status, settings, catalog, local, budget, onSet, onOpenSettings, onEscalate, onDeescalate, onBudgetOverride }: {
  status: Status | null
  settings: SettingsDoc | null
  catalog: CatalogModel[]
  local: LocalStatus | null
  budget: BudgetSnapshot | null
  onSet: (key: string, value: any) => void
  onOpenSettings: (tab?: string) => void
  onEscalate: (harder: boolean) => void
  onDeescalate: () => void
  onBudgetOverride: (clearKeyCap: boolean) => void
}) {
  const [open, setOpen] = useState<string | null>(null)
  const ref = useRef<HTMLDivElement>(null)

  useDismissable(open !== null, () => setOpen(null), ref)

  const spec = (key: string): SettingSpec | undefined =>
    settings?.settings.find((s) => s.key === key)

  const policy = status?.policy ?? null
  const mode = policy?.policy ?? String(spec('policy')?.value ?? 'local-first')
  const model = String(spec('model')?.value ?? status?.model ?? '—')
  const row = catalog.find((m) => String(m.id) === model)
  const inPrice = row ? pricePerM(row, 'prompt') : null

  // What runs right now versus what is configured: under gateway routing or
  // an escalation they differ for a whole turn.
  const live = !!status?.busy
  const turnModel = status?.turn_model || null
  const localId = local?.default_model ?? 'local/auto'
  const localJob = local?.jobs.find((j) => j.kind === 'model' && j.target === localId) ?? null
  const localInstalled = !!local?.models.find((m) => m.id === localId)?.installed
  const localName = local?.models.find((m) => m.id === localId)?.label ?? shortModel(localId)
  const escalated = policy?.escalated ?? null
  const tier = policy?.tier ?? (mode === 'cloud-auto' ? 'cloud_auto' : mode === 'pinned' ? 'pinned' : 'local')
  const pending = mode === 'cloud-auto' && !turnModel
  const lit = tier === 'escalated' || mode === 'cloud-auto'
  const value = tier === 'escalated' && escalated ? `Escalated · ${shortModel(escalated)}`
    : mode === 'cloud-auto' ? (turnModel ? shortModel(turnModel) : 'Gateway picks')
    : mode === 'pinned' ? shortModel(turnModel ?? model)
    : localJob ? `Local (${Math.round((100 * localJob.bytes) / Math.max(localJob.total, 1))}%)`
    : !localInstalled && local ? 'Local (download needed)'
    : `Local · ${shortModel(localId)}`
  const writerLine = mode === 'local-first'
    ? (localInstalled ? `${localName} on this machine · free` : 'Local model not ready · cloud fallback answers')
    : mode === 'cloud-auto' ? 'Mesh’s Auto Router picks per prompt'
    : row ? `${shortModel(model)} · ${fmtUsd(inPrice)} in · ${fmtUsd(pricePerM(row, 'completion'))} out, per 1M`
          : shortModel(model)
  // Session totals only advance when a turn ENDS, so the in-flight turn has
  // to be added in or the numbers sit frozen for the whole run.
  const tokens = (status?.session_tokens ?? 0) + (live ? (status?.turn_tokens ?? 0) : 0)
  const cost = (status?.session_cost ?? 0) + (live ? (status?.turn_cost ?? 0) : 0)
  const estimated = !!status?.turn_estimated

  const toggle = (id: string) => setOpen((o) => (o === id ? null : id))

  return (
    <div className="controlbar" ref={ref}>
      {/* One pill names who writes right now; its popover holds the policy
          switch, the ladder and the Escalate controls. */}
      <div className="ctl ctl-model">
        <button className={`ctl-btn ${open === 'model' ? 'on' : ''} ${pending ? 'pending' : ''} ${lit ? 'lit' : ''}`}
                onClick={() => toggle('model')}
                title={policy?.escalated_reason ? `Escalated: ${policy.escalated_reason}` : writerLine}>
          <span className="ctl-label">{tier === 'escalated' ? 'Cloud' : mode === 'local-first' ? 'Writer' : 'Model'}</span>
          <span className="ctl-value">{value}</span>
          {status?.turn_local && <span className="tag local" title="Runs on this machine, free">local</span>}
          {policy?.escalation_pending && tier !== 'escalated' && <span className="ctl-flag" title="the next turn runs on the strong model">next</span>}
        </button>
        {open === 'model' && (
          <Pop title="Policy" onSettings={() => { setOpen(null); onOpenSettings('routing') }}>
            {spec('policy') && <Field spec={spec('policy')!} onChange={onSet} compact label="Who writes" />}

            <div className="ladder">
              <div className="ladder-row"><span>Writes</span><b>{writerLine}</b></div>
              {mode === 'local-first' && (
                <>
                  <div className="ladder-row"><span>Audits</span><b>{policy?.audit_model ? shortModel(policy.audit_model) : '—'} <i>soon</i></b></div>
                  <div className="ladder-row"><span>Escalates to</span><b>{policy?.escalation_model ? shortModel(policy.escalation_model) : '—'}</b></div>
                </>
              )}
            </div>

            {mode === 'local-first' && (
              <div className="ladder-actions">
                {tier === 'escalated' || policy?.escalation_pending
                  ? <button className="btn subtle" onClick={() => { setOpen(null); onDeescalate() }}>Back to local</button>
                  : <>
                      <button className="btn primary" onClick={() => { setOpen(null); onEscalate(false) }}>
                        {live ? 'Escalate now' : 'Escalate next turn'}
                      </button>
                      <button className="btn subtle" onClick={() => { setOpen(null); onEscalate(true) }} title="One reasoning level higher, for this escalation">Harder</button>
                    </>}
              </div>
            )}
            {mode === 'pinned' && (
              <button className="btn subtle" onClick={() => { setOpen(null); onOpenSettings('model') }}>Change model</button>
            )}
            {mode === 'cloud-auto' && (
              <p className="pop-note">Mesh’s Auto Router picks upstream, so the model is known once the first hop answers.</p>
            )}
          </Pop>
        )}
      </div>

      {/* ---- usage ring: today's share of the cloud budget ---- */}
      {budget && (
        <div className="ctl ctl-usage">
          <button className={`ctl-btn ring-btn ${open === 'usage' ? 'on' : ''}`} onClick={() => toggle('usage')}
                  title={budget.key_cap ? 'Mesh key spend cap reached' : `Today ${budget.day.pct}% · this month ${budget.month.pct}%`}
                  aria-label="Usage">
            <Ring pct={budget.key_cap ? 100 : budget.day.pct} tone={ringTone(budget)} />
          </button>
          {open === 'usage' && (
            <Pop title="Usage" onSettings={() => { setOpen(null); onOpenSettings('budget') }}>
              <UsageBar label="Today" w={budget.day} limited={budget.month.limit > 0} resets={`Resets at midnight${budget.day.override ? ' · extra allowed today' : ''}`} />
              <UsageBar label="This month" w={budget.month} limited={budget.month.limit > 0} resets={`Resets ${new Date(budget.month.resets * 1000).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })}`} />
              {Object.keys(budget.month.by_kind).length > 0 && (
                <dl className="pop-stats">
                  {Object.entries(budget.month.by_kind).sort((a, b) => b[1] - a[1]).map(([k, v]) => (
                    <div key={k}><dt>{KIND_LABEL[k] ?? k}</dt><dd>${v.toFixed(4)}</dd></div>
                  ))}
                </dl>
              )}
              <p className="pop-note">
                {budget.key_cap
                  ? 'Your Mesh key’s own spend cap was reached. Raise it at app.meshapi.ai, then clear the hold.'
                  : 'Today’s share is what is left this month divided by the days left. Work on this machine is never counted.'}
              </p>
              {budget.key_cap
                ? <button className="btn subtle" onClick={() => { setOpen(null); onBudgetOverride(true) }}>I raised the cap</button>
                : canAllowMoreToday(budget)
                  ? <button className="btn primary" onClick={() => { setOpen(null); onBudgetOverride(false) }}>Allow more today</button>
                  : null}
            </Pop>
          )}
        </div>
      )}

      {/* ---- tokens ---- */}
      <div className="ctl ctl-tok">
        <button className={`ctl-btn ${open === 'tok' ? 'on' : ''} ${live ? 'ticking' : ''}`}
                onClick={() => toggle('tok')}
                title={`${tokens.toLocaleString()} tokens`}>
          <span className="ctl-label">Tokens</span>
          <span className="ctl-value">{fmtTokens(tokens)} tok</span>
        </button>
        {open === 'tok' && (
          <Pop title="Tokens" onSettings={() => { setOpen(null); onOpenSettings('limits') }}>
            <dl className="pop-stats">
              <div><dt>This turn</dt><dd>{(status?.turn_tokens ?? 0).toLocaleString()}</dd></div>
              <div><dt>Session</dt><dd>{(status?.session_tokens ?? 0).toLocaleString()}</dd></div>
            </dl>
            <p className="pop-note">
              Counted from the usage the gateway reports on every hop, so it
              includes tool results and re-sent history — not just your prompt.
            </p>
          </Pop>
        )}
      </div>

      {/* ---- cost ---- */}
      <div className="ctl ctl-cost">
        <button className={`ctl-btn ${open === 'cost' ? 'on' : ''} ${live ? 'ticking' : ''}`}
                onClick={() => toggle('cost')}>
          <span className="ctl-label">Session</span>
          <span className="ctl-value money">
            {estimated && '~'}
            {cost.toLocaleString(undefined,
              { style: 'currency', currency: 'USD', minimumFractionDigits: 4 })}
          </span>
        </button>
        {open === 'cost' && (
          <Pop title="Spend" onSettings={() => { setOpen(null); onOpenSettings('behaviour') }}>
            <dl className="pop-stats">
              <div><dt>This turn</dt><dd>${(status?.turn_cost ?? 0).toFixed(6)}</dd></div>
              <div><dt>Session</dt><dd>${(status?.session_cost ?? 0).toFixed(6)}</dd></div>
              <div><dt>Tokens</dt><dd>{tokens.toLocaleString()}</dd></div>
              <div><dt>Rate</dt><dd>{fmtUsd(inPrice)} / 1M in</dd></div>
            </dl>
            <p className="pop-note">
              {status?.turn_local && (status.turn_cost ?? 0) === 0
                ? 'This turn ran on your machine — nothing was billed.'
                : estimated
                ? 'Marked ~ because this model reported no cost, so this is '
                  + 'computed from the catalog’s own per-token rates.'
                : cost <= 0 && tokens > 0
                  ? 'The gateway reported no cost for this model and it could not '
                    + 'be priced from the catalog, so this is not what you were billed.'
                  : 'What the gateway actually billed, not an estimate.'}
            </p>
          </Pop>
        )}
      </div>

    </div>
  )
}

const KIND_LABEL: Record<string, string> = {
  escalate: 'Escalations', audit: 'Audits', auto: 'Gateway auto', pinned: 'Pinned model', fallback: 'Cloud fallback', other: 'Other',
}

/** A 16px ring filled clockwise to `pct`, like a battery for the day's budget. */
function Ring({ pct, tone }: { pct: number; tone: string }) {
  const r = 6, c = 2 * Math.PI * r
  const fill = Math.max(0, Math.min(100, pct)) / 100
  return (
    <svg className={`ring ${tone}`} viewBox="0 0 16 16" width="16" height="16" aria-hidden>
      <circle cx="8" cy="8" r={r} className="ring-track" />
      <circle cx="8" cy="8" r={r} className="ring-fill" strokeDasharray={`${c * fill} ${c}`} transform="rotate(-90 8 8)" />
    </svg>
  )
}

function UsageBar({ label, w, limited, resets }: {
  label: string; w: BudgetSnapshot['day']; limited: boolean; resets: string
}) {
  const over = limited && w.pct >= 100
  return (
    <div className="usage">
      <div className="usage-head"><strong>{label}</strong><span>{limited ? `${w.pct}% used` : `$${w.spent.toFixed(2)} · no limit`}</span></div>
      <div className="usage-bar" role="progressbar" aria-label={label} aria-valuenow={Math.min(100, w.pct)} aria-valuemin={0} aria-valuemax={100}>
        <span className={over ? 'over' : w.pct >= 80 ? 'warn' : ''} style={{ width: `${Math.min(100, w.pct)}%` }} />
      </div>
      <div className="usage-foot"><span>{resets}</span><span>${w.spent.toFixed(2)}{limited ? ` of $${w.limit.toFixed(2)}` : ''}</span></div>
    </div>
  )
}

function Pop({ title, children, onSettings }: {
  title: string; children: React.ReactNode; onSettings: () => void
}) {
  return (
    <div className="pop">
      <div className="pop-head">{title}</div>
      {children}
      <button className="pop-more" onClick={onSettings}>All settings →</button>
    </div>
  )
}

const shortModel = (m: string) => (m.includes('/') ? m.split('/').slice(1).join('/') : m)

