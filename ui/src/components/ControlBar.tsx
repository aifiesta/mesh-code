import { useRef, useState } from 'react'
import { useDismissable } from '../useDismissable'
import type { RouteExplain, SettingSpec, SettingsDoc, Status } from '../types'
import { fmtTokens, fmtUsd, pricePerM, type CatalogModel } from '../types'
import { Field } from './Field'

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
export function ControlBar({ status, settings, catalog, routeExplain, onSet, onOpenSettings, onExplain }: {
  status: Status | null
  settings: SettingsDoc | null
  catalog: CatalogModel[]
  routeExplain: RouteExplain | null
  onSet: (key: string, value: any) => void
  onOpenSettings: (tab?: string) => void
  onExplain: () => void
}) {
  const [open, setOpen] = useState<string | null>(null)
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

  const toggle = (id: string) => setOpen((o) => (o === id ? null : id))

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
          {routedAway && <span className="ctl-flag" title="chosen by the router">routed</span>}
        </button>
        {open === 'model' && (
          <Pop title="Model" onSettings={() => { setOpen(null); onOpenSettings(routing ? 'routing' : 'model') }}>
            {spec('route_mode') && <Field spec={spec('route_mode')!} onChange={onSet} compact label="Who picks" />}

            {!routing && (
              <div className="pinned-model">
                <span className="pinned-name" title={model}>{shortModel(model)}</span>
                <span className="pinned-price">
                  {row ? <>{fmtUsd(inPrice)} in · {fmtUsd(pricePerM(row, 'completion'))} out, per 1M</>
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
              {estimated
                ? 'Marked ~ because this model reported no cost, so this is '
                  + 'computed from the catalog’s own per-token rates.'
                : cost <= 0 && tokens > 0
                  ? 'The gateway reported no cost for this model and it could not '
                    + 'be priced from the catalog, so this is not what you were billed.'
                  : 'What the gateway actually billed, not an estimate.'}
            </p>
            {spec('optimize') && <Field spec={spec('optimize')!} onChange={onSet} compact />}
          </Pop>
        )}
      </div>

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

/** Why the router would pick what it picks — before you spend anything. */
function Explain({ explain }: { explain: RouteExplain | null }) {
  if (!explain) return null
  if (!explain.available) {
    return (
      <p className="pop-note">
        {explain.cohort
          ? <>Reads as a <b>{pretty(explain.cohort)}</b> prompt
              {explain.difficulty && <> ({explain.difficulty} difficulty)</>}.
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
        <span className="explain-model">{top}</span>
        <span className="explain-cap">
          Reads as <b>{pretty(explain.cohort)}</b>
          {explain.difficulty && <> · {explain.difficulty} difficulty</>}
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

const shortModel = (m: string) => (m.includes('/') ? m.split('/').slice(1).join('/') : m)

/** Engine data is rendered as-is, so never assume a field's runtime type —
 *  a stray non-string here previously threw and blanked the whole window. */
const pretty = (v: unknown) =>
  typeof v === 'string' ? v.replace(/[-_]/g, ' ') : String(v ?? '')
