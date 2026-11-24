import { useRef, useState } from 'react'
import { useDismissable } from '../useDismissable'
import type { DailyCap, LocalStatus, RouteExplain, SettingSpec, SettingsDoc, Status } from '../types'
import { fmtTokens, fmtUsd, pricePerM, type CatalogModel } from '../types'
import { Field } from './Field'
import { BillTable } from './BillSplit'
import { converting, fxNote, useMoney } from '../money'

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
export function ControlBar({ status, settings, catalog, routeExplain, local, onSet, onOpenSettings, onExplain, onEscalate, onBackToLocal }: {
  status: Status | null
  settings: SettingsDoc | null
  catalog: CatalogModel[]
  routeExplain: RouteExplain | null
  local?: LocalStatus | null
  onSet: (key: string, value: any) => void
  onOpenSettings: (tab?: string) => void
  onExplain: () => void
  /** Off the local model to Smart routing's pick; `harder`: one step beyond it. */
  onEscalate?: (harder: boolean) => void
  /** Back to the local model (the engine refuses, with a reason, for a long chat). */
  onBackToLocal?: () => void
}) {
  const [open, setOpen] = useState<string | null>(null)
  const { fx, money, price } = useMoney()
  const ref = useRef<HTMLDivElement>(null)

  useDismissable(open !== null, () => setOpen(null), ref)

  const spec = (key: string): SettingSpec | undefined =>
    settings?.settings.find((s) => s.key === key)

  const routeMode = String(spec('route_mode')?.value ?? 'off')
  const routeLabel = spec('route_mode')?.options?.find((o) => o.value === routeMode)?.label ?? 'Pinned'
  const model = String(spec('model')?.value ?? status?.model ?? '—')
  const row = catalog.find((m) => String(m.id) === model)
  const inPrice = row ? pricePerM(row, 'prompt') : null

  // What is running RIGHT NOW versus what is configured. With routing on
  // these differ constantly, and the bar used to show only the configured
  // one — so it read "claude-sonnet-4.5" through an entire turn that in fact
  // ran on something else.
  const live = !!status?.busy
  const turnModel = status?.turn_model || null
  const routing = routeMode !== 'off'
  const routedAway = !!turnModel && turnModel !== model
  // With routing on and nothing run yet, naming the pinned model is simply
  // wrong — the router has not chosen, and it usually will not choose that
  // one. Name what DECIDES until there is a real answer to show.
  const pending = routing && !turnModel
  const shownModel = turnModel
    ?? (routing ? (routeMode === 'auto' ? 'Gateway picks' : 'Router picks') : model)
  // Session totals only advance when a turn ENDS, so the in-flight turn has
  // to be added in or the numbers sit frozen for the whole run.
  const tokens = (status?.session_tokens ?? 0) + (live ? (status?.turn_tokens ?? 0) : 0)
  const cost = (status?.session_cost ?? 0) + (live ? (status?.turn_cost ?? 0) : 0)
  const estimated = !!status?.turn_estimated

  // The local model inside Smart routing: which buttons make sense now.
  const onLocal = !!status?.turn_local
  const localReady = !!local?.enabled && local.server.state === 'ready'
  // Today's share of a monthly plan: past it, cloud models wait for tomorrow,
  // so there is nothing to escalate to.
  const cap = status?.daily_cap
  const capped = !!cap?.enabled && cap.capped
  // Past today's share a downloaded local model is woken for the task, so
  // "on" means downloaded and turned on, not only loaded right now.
  const localOn = !!local?.enabled && (local.models ?? []).some((m) => m.installed)
  const showEscalate = live && onLocal && routeMode === 'smart' && !capped
  const showBack = live && !onLocal && localReady && routeMode === 'smart'

  const toggle = (id: string) => setOpen((o) => (o === id ? null : id))
  const usd6 = (n: number) => '$' + n.toFixed(6)
  // Converted, the dollar bill is one hover away.
  const usdTitle = (n?: number) => (converting(fx) ? `${usd6(n ?? 0)} billed` : undefined)

  return (
    <div className="controlbar" ref={ref}>
      {/* ---- model + routing, one pill ----
          These were two pills that said the same thing twice ("Router picks"
          next to "Smart (local)"). Now one pill names what decides the model
          — the pinned model, or the router — and its popover holds the
          routing switch, the effort dial and the router's pick together. */}
      <div className="ctl ctl-model">
        <button className={`ctl-btn ${open === 'model' ? 'on' : ''} ${pending ? 'pending' : ''} ${routing ? 'lit' : ''}`}
                onClick={() => { toggle('model'); if (routing) onExplain() }}
                title={pending
                  ? `${routeLabel} routing — the model is chosen per prompt (fallback: ${model})`
                  : routedAway ? `Routed to ${turnModel} (pinned: ${model})` : shownModel}>
          <span className="ctl-label">Model</span>
          <span className="ctl-value">
            {pending ? (routeMode === 'auto' ? 'Auto routing' : 'Smart routing') : shortModel(shownModel)}
          </span>
          {onLocal
            ? <span className="ctl-flag local" title="running on this machine, free">local</span>
            : routedAway && <span className="ctl-flag" title="chosen by the router">routed</span>}
        </button>
        {open === 'model' && (
          <Pop title="Model" onSettings={() => { setOpen(null); onOpenSettings(routing ? 'routing' : 'model') }}>
            {spec('route_mode') && <Field spec={spec('route_mode')!} onChange={onSet} compact label="Who picks" />}

            {!routing && (
              <div className="pinned-model">
                <span className="pinned-name" title={model}>{shortModel(model)}</span>
                <span className="pinned-price">
                  {row ? <>{price(inPrice, fmtUsd)} in · {price(pricePerM(row, 'completion'), fmtUsd)} out, per 1M</>
                       : 'Pricing loads with the catalog.'}
                </span>
                <button className="btn subtle" onClick={() => { setOpen(null); onOpenSettings('model') }}>
                  Change model
                </button>
              </div>
            )}

            {routeMode === 'smart' && spec('route_effort') &&
              <Field spec={spec('route_effort')!} onChange={onSet} compact label="Effort" />}
            {routeMode === 'smart' && <Explain explain={routeExplain} />}
            {routeMode === 'auto' && (
              <p className="pop-note">Mesh’s Auto Router picks upstream, so the model is known once the first hop answers.</p>
            )}

            {routing && (
              <p className="pop-fallback">
                {routedAway
                  ? <>This turn ran on <b>{shortModel(turnModel!)}</b>. </>
                  : null}
                Fallback when the router can’t pick: <b>{shortModel(model)}</b>
                <button className="link" onClick={() => { setOpen(null); onOpenSettings('model') }}>change</button>
              </p>
            )}
          </Pop>
        )}
      </div>

      {(showEscalate || showBack) && (
        <div className="ctl ctl-local">
          {showEscalate && onEscalate && (
            <>
              <button className="ctl-btn" onClick={() => onEscalate(false)}
                      title="Hand this task to Smart routing's cloud pick for its level">
                <span className="ctl-value">Escalate</span>
              </button>
              <button className="ctl-btn" onClick={() => onEscalate(true)}
                      title="Hand it to the model Smart routing would step up to from that pick">
                <span className="ctl-value">Harder</span>
              </button>
            </>
          )}
          {showBack && onBackToLocal && (
            <button className="ctl-btn" onClick={onBackToLocal}
                    title="Continue this task on the local model (refused for a chat over half its window)">
              <span className="ctl-value">Back to local</span>
            </button>
          )}
        </div>
      )}

      {/* ---- today's share of the monthly plan (hidden when no plan is set) ---- */}
      {cap?.enabled && (
        <div className="ctl ctl-day">
          <button className={`ctl-btn ${open === 'day' ? 'on' : ''} ${capped ? 'capped' : ''}`}
                  onClick={() => toggle('day')}
                  title={dayTitle(cap, localOn)}>
            <Ring left={cap.left_pct} />
            <span className="ctl-value">
              {capped ? 'Today used up' : <>Today {Math.floor(cap.left_pct)}% left</>}
            </span>
          </button>
          {open === 'day' && (
            <Pop title="Today">
              <div className="day-hero">
                <Ring left={cap.left_pct} big />
                <div>
                  <div className="day-big">{capped ? 'Used up for today' : 'left for today'}</div>
                  <div className="day-sub">
                    {capped
                      ? (localOn ? 'Free local model until tomorrow' : 'Cloud models back tomorrow')
                      : 'Unused share carries over'}
                  </div>
                </div>
              </div>
              <div className="day-plan">
                <span>Next share</span>
                <b>{fmtDay(cap.next_share_on)}</b>
              </div>
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
            <BillTable session={status?.session_bill} turn={status?.turn_bill} live={live} />
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
            {money(cost, (n) => n.toLocaleString(undefined,
              { style: 'currency', currency: 'USD', minimumFractionDigits: 4 }), estimated)}
          </span>
        </button>
        {open === 'cost' && (
          <Pop title="Spend" onSettings={() => { setOpen(null); onOpenSettings('routing') }}>
            <dl className="pop-stats">
              <div><dt>This turn</dt><dd title={usdTitle(status?.turn_cost)}>{money(status?.turn_cost ?? 0, usd6)}</dd></div>
              <div><dt>Session</dt><dd title={usdTitle(status?.session_cost)}>{money(status?.session_cost ?? 0, usd6)}</dd></div>
              <div><dt>Tokens</dt><dd>{tokens.toLocaleString()}</dd></div>
              <div><dt>Rate</dt><dd>{price(inPrice, fmtUsd)} / 1M in</dd></div>
            </dl>
            <BillTable session={status?.session_bill} turn={status?.turn_bill} live={live} />
            <p className="pop-note">
              {estimated
                ? 'Marked ~ because this model reported no cost, so this is '
                  + 'computed from the catalog’s own per-token rates.'
                : cost <= 0 && tokens > 0
                  ? 'The gateway reported no cost for this model and it could not '
                    + 'be priced from the catalog, so this is not what you were billed.'
                  : 'What the gateway actually billed, not an estimate.'}
            </p>
            {fxNote(fx) && <p className="pop-note">{fxNote(fx)}</p>}
            {spec('currency') && <Field spec={spec('currency')!} onChange={onSet} compact />}
          </Pop>
        )}
      </div>

    </div>
  )
}

function Pop({ title, children, onSettings }: {
  title: string; children: React.ReactNode; onSettings?: () => void
}) {
  return (
    <div className="pop">
      <div className="pop-head">{title}</div>
      {children}
      {onSettings && <button className="pop-more" onClick={onSettings}>All settings →</button>}
    </div>
  )
}

/** Why the router would pick what it picks — before you spend anything. */
function Explain({ explain }: { explain: RouteExplain | null }) {
  if (!explain) return null
  if (!explain.available) {
    return (
      <p className="pop-note">
        {explain.cohort
          ? <>Reads as a <b>{pretty(explain.cohort)}</b> prompt
              {explain.task ? <> ({explain.task})</> : explain.difficulty && <> ({explain.difficulty} difficulty)</>}.
              {explain.reason && <> Can’t pick yet — {explain.reason}.</>}</>
          : explain.reason}
      </p>
    )
  }
  const ranked = explain.ranked.map((r: any) => shortModel(String(r.model ?? r.id ?? r)))
  const [top, ...rest] = ranked
  return (
    <div className="explain">
      <div className="explain-top">
        <span className="explain-cap">For this prompt the router would pick</span>
        <span className="explain-model">
          {top}{explain.thinking && explain.thinking !== 'default' && <> · {explain.thinking} thinking</>}
        </span>
        <span className="explain-cap">
          {explain.task
            ? <>Reads as a <b>{explain.task}</b>{explain.why && <>: {explain.why}</>}</>
            : <>Reads as <b>{pretty(explain.cohort)}</b>
                {explain.difficulty && <> · {explain.difficulty} difficulty</>}</>}
        </span>
      </div>
      {rest.length > 0 && (
        <p className="explain-rest">
          Then {rest.slice(0, 3).join(', ')}
        </p>
      )}
    </div>
  )
}

/** A ring: how much of today's share is left. The big one has the number inside. */
function Ring({ left, big }: { left: number; big?: boolean }) {
  const size = big ? 64 : 14, stroke = big ? 6 : 2.25
  const r = (size - stroke) / 2, c = 2 * Math.PI * r
  const frac = Math.max(0, Math.min(1, left / 100))
  return (
    <svg className="day-ring" width={size} height={size} viewBox={`0 0 ${size} ${size}`} aria-hidden="true">
      <circle cx={size / 2} cy={size / 2} r={r} fill="none" strokeWidth={stroke} className="day-ring-track" />
      {frac > 0 && <circle cx={size / 2} cy={size / 2} r={r} fill="none" strokeWidth={stroke} className="day-ring-left"
              strokeDasharray={`${c * frac} ${c}`} strokeLinecap="round"
              transform={`rotate(-90 ${size / 2} ${size / 2})`} />}
      {big && (
        <text x="50%" y="50%" textAnchor="middle" dominantBaseline="central" className="day-ring-num">
          {Math.floor(left)}%
        </text>
      )}
    </svg>
  )
}

function fmtDay(iso: string | null | undefined): string {
  if (!iso) return '—'
  const d = new Date(iso + 'T00:00:00')
  if (isNaN(d.getTime())) return iso
  return d.toLocaleDateString(undefined, { weekday: 'short', day: 'numeric', month: 'short' })
}

/** The chip's tooltip: shares and dates only, never money. */
function dayTitle(cap: Extract<DailyCap, { enabled: true }>, localOn: boolean): string {
  if (cap.capped) {
    return `Today's share of your plan is used. Cloud models are back ${fmtDay(cap.next_share_on)}`
      + (localOn ? '; until then the free local model takes new tasks.' : '.')
  }
  return `${Math.floor(cap.left_pct)}% of today's share of your plan left. A new share starts ${fmtDay(cap.next_share_on)}.`
}

const shortModel = (m: string) => (m.includes('/') ? m.split('/').slice(1).join('/') : m)

/** Engine data is rendered as-is, so never assume a field's runtime type —
 *  a stray non-string here previously threw and blanked the whole window. */
const pretty = (v: unknown) =>
  typeof v === 'string' ? v.replace(/[-_]/g, ' ') : String(v ?? '')
