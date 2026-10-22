import { useRef, useState } from 'react'
import { useDismissable } from '../useDismissable'
import type { LocalStatus, SettingSpec, SettingsDoc, Status } from '../types'
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
export function ControlBar({ status, settings, catalog, local, onSet, onOpenSettings }: {
  status: Status | null
  settings: SettingsDoc | null
  catalog: CatalogModel[]
  local: LocalStatus | null
  onSet: (key: string, value: any) => void
  onOpenSettings: (tab?: string) => void
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

  // What runs right now versus what is configured: with routing on they
  // differ for a whole turn.
  const live = !!status?.busy
  const turnModel = status?.turn_model || null
  const isLocal = model.startsWith('local/')
  const routing = routeMode !== 'off' && !isLocal      // a local pin is never routed
  const routedAway = !!turnModel && turnModel !== model
  // With routing on and nothing run yet, naming the pinned model is simply
  // wrong — the router has not chosen, and it usually will not choose that
  // one. Name what DECIDES until there is a real answer to show.
  const pending = routing && !turnModel
  const localId = row?.resolves_to ?? model
  const localJob = local?.jobs.find((j) => j.kind === 'model' && j.target === localId) ?? null
  const localInstalled = !!local?.models.find((m) => m.id === localId)?.installed
  const localName = local?.models.find((m) => m.id === localId)?.label ?? shortModel(localId)
  const shownModel = turnModel
    ?? (routing ? 'Gateway picks' : model)
  // Session totals only advance when a turn ENDS, so the in-flight turn has
  // to be added in or the numbers sit frozen for the whole run.
  const tokens = (status?.session_tokens ?? 0) + (live ? (status?.turn_tokens ?? 0) : 0)
  const cost = (status?.session_cost ?? 0) + (live ? (status?.turn_cost ?? 0) : 0)
  const estimated = !!status?.turn_estimated

  const toggle = (id: string) => setOpen((o) => (o === id ? null : id))

  return (
    <div className="controlbar" ref={ref}>
      {/* One pill names what decides the model; its popover holds the
          routing switch, the effort dial and the router's pick. */}
      <div className="ctl ctl-model">
        <button className={`ctl-btn ${open === 'model' ? 'on' : ''} ${pending ? 'pending' : ''} ${routing ? 'lit' : ''}`}
                onClick={() => toggle('model')}
                title={pending
                  ? `${routeLabel} routing — the model is chosen per prompt (fallback: ${model})`
                  : routedAway ? `Routed to ${turnModel} (pinned: ${model})` : shownModel}>
          <span className="ctl-label">Model</span>
          <span className="ctl-value">
            {pending ? 'Auto routing'
              : isLocal && !turnModel && localJob ? `Local (${Math.round((100 * localJob.bytes) / Math.max(localJob.total, 1))}%)`
              : isLocal && !turnModel && !localInstalled && local ? 'Local (download needed)'
              : isLocal && !turnModel ? `Local · ${shortModel(localId)}`
              : shortModel(shownModel)}
          </span>
          {routedAway && <span className="ctl-flag" title="chosen by the gateway">routed</span>}
          {(status?.turn_local || (isLocal && !turnModel && localInstalled)) && <span className="tag local" title="Runs on this machine, free">local</span>}
        </button>
        {open === 'model' && (
          <Pop title="Model" onSettings={() => { setOpen(null); onOpenSettings(routing ? 'routing' : 'model') }}>
            {spec('route_mode') && <Field spec={spec('route_mode')!} onChange={onSet} compact label="Who picks" />}

            {!routing && (
              <div className="pinned-model">
                <span className="pinned-name" title={isLocal ? localId : model}>{isLocal ? localName : shortModel(model)}</span>
                <span className="pinned-price">
                  {isLocal ? 'free · on this machine'
                    : row ? <>{fmtUsd(inPrice)} in · {fmtUsd(pricePerM(row, 'completion'))} out, per 1M</>
                         : 'Pricing loads with the catalog.'}
                </span>
                <button className="btn subtle" onClick={() => { setOpen(null); onOpenSettings('model') }}>
                  Change model
                </button>
              </div>
            )}

            {routeMode === 'auto' && (
              <p className="pop-note">{isLocal
                ? 'A local model pin is never routed; pick a cloud model for the gateway to route.'
                : 'Mesh’s Auto Router picks upstream, so the model is known once the first hop answers.'}</p>
            )}

            {routing && (
              <p className="pop-fallback">
                {routedAway
                  ? <>This turn ran on <b>{shortModel(turnModel!)}</b>. </>
                  : null}
                Fallback when the gateway can’t pick: <b>{shortModel(model)}</b>
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

