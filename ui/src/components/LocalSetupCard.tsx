import type { LocalJob, LocalStatus } from '../types'
import { fmtBytes } from '../types'

/** First-run question of the local layer ("download this model?"), later
 *  the banner for a download in flight or a stopped / failed server. Nothing
 *  is downloaded until the user says yes here or in Settings. */
export function LocalSetupCard({ local, compact, note, onInstall, onCancel, onDecline, onNotNow, onStart, onOpenSettings }: {
  local: LocalStatus
  /** Banner form: one line of status, the actions, no explanation. */
  compact?: boolean
  /** The layer's last word outside any chat (a failed download). */
  note?: { level: string; text: string } | null
  onInstall: (model: string) => void
  onCancel: (job: string) => void
  /** "No thanks": never ask again (still installable from Settings). */
  onDecline: () => void
  /** "Not now": hide until the app is opened again. */
  onNotNow?: () => void
  onStart?: () => void
  /** Absent on the key gate, which has no settings sheet to open. */
  onOpenSettings?: () => void
}) {
  const rec = local.recommended
  const job = local.jobs.find((j) => j.kind === 'model') ?? local.jobs[0] ?? null
  const installed = local.models.find((m) => m.id === local.default_model)?.installed
  const hw = local.hardware
  const where = onOpenSettings ? 'under Settings › Local model' : 'under Settings › Local model once you are in the app'

  let body: React.ReactNode
  if (!local.enabled) {
    body = <Line head="Local model is off" sub={`Turn it on ${where} to code without the cloud.`} />
  } else if (!local.booted) {
    body = <Line head="Checking this machine…" sub="Reading memory and GPU to pick a model that fits." />
  } else if (local.boot_error) {
    body = <Line head="The local model layer could not start" sub={local.boot_error} tone="warn" />
  } else if (local.probe_failed) {
    body = <Line head="Could not read this machine's memory" sub={`Cloud models keep working. Pick a local model by hand ${where}.`} />
  } else if (job) {
    body = <Progress job={job} onCancel={() => onCancel(job.id)} />
  } else if (local.server.state === 'starting') {
    body = <Line head="Starting the local model…" sub="The first load takes a moment while the weights page in." />
  } else if (local.server.error && local.server.state === 'failed') {
    body = <Line head="The local model failed to start" sub={local.server.error} tone="warn" />
  } else if (installed && local.server.held) {
    body = (
      <>
        <Line head="The local model is stopped" sub={local.server.error ?? 'Smart routing uses the cloud until you start it again.'} />
        {onStart && <div className="local-actions"><button className="btn subtle" onClick={onStart}>Start</button></div>}
      </>
    )
  } else if (installed) {
    body = <Line head={`${label(local, local.default_model)} is ready`} sub="With Smart routing on, lookups and small changes start on this machine for free; harder work and anything it struggles with goes to the cloud." tone="ok" />
  } else if (local.offer) {
    const o = local.offer
    body = (
      <>
        <Line head={`Run small tasks on this machine? ${o.label}, ${fmtBytes(o.bytes)}`}
              sub={`${local.recommended?.reason ?? ''} With Smart routing on, lookups and small changes start on it for free and step up to the cloud when it struggles. Cloud models work while it downloads.`} />
        <div className="local-actions">
          <button className="btn primary" onClick={() => onInstall(o.model)}>Download {fmtBytes(o.bytes)}</button>
          {onNotNow && <button className="btn subtle" onClick={onNotNow}>Not now</button>}
          <button className="btn ghost" onClick={onDecline}>No thanks</button>
        </div>
      </>
    )
  } else if (local.needs_confirm && rec) {
    const why = local.needs_confirm.reason === 'slow'
      ? `This machine has no GPU the model can use, so ${rec.model.replace('local/', '')} would be slow.`
      : `${label(local, rec.model)} is ${fmtBytes(local.needs_confirm.bytes)} — download it?`
    body = (
      <>
        <Line head={local.needs_confirm.reason === 'slow' ? 'Local model would be slow here' : 'A bigger model fits this machine'}
              sub={why} />
        <div className="local-actions">
          {local.needs_confirm.reason !== 'slow' &&
            <button className="btn primary" onClick={() => onInstall(rec.model)}>Download {fmtBytes(local.needs_confirm.bytes)}</button>}
          {onOpenSettings && <button className="btn subtle" onClick={onOpenSettings}>Choose another</button>}
          <button className="btn ghost" onClick={onNotNow ?? onDecline}>Not now</button>
        </div>
      </>
    )
  } else if (rec && (!local.declined || !onOpenSettings)) {
    body = (
      <>
        <Line head={`Recommended: ${label(local, rec.model)}`} sub={rec.reason} />
        <div className="local-actions">
          <button className="btn primary" onClick={() => onInstall(rec.model)}>Download</button>
          <button className="btn ghost" onClick={onNotNow ?? onDecline}>Not now</button>
        </div>
      </>
    )
  } else {
    body = <Line head="No local model installed" sub={`Pick one ${where} whenever you like.`} />
  }

  return (
    <section className={`local-card ${compact ? 'compact' : ''}`}>
      <div className="local-head">
        <span className="local-dot" data-state={local.server.state} aria-hidden />
        <strong>Local model</strong>
        {hw && <span className="local-hw">{hw.ram_gb} GB · {hw.gpu.kind === 'none' ? 'CPU' : hw.gpu.kind}</span>}
        <div className="spacer" />
        {onOpenSettings && <button className="link" onClick={onOpenSettings}>Manage</button>}
      </div>
      {body}
      {note && <div className={`local-line ${note.level === 'warn' ? 'warn' : ''}`}><span>{note.text}</span></div>}
    </section>
  )
}

function Line({ head, sub, tone }: { head: string; sub?: string; tone?: 'ok' | 'warn' }) {
  return (
    <div className={`local-line ${tone ?? ''}`}>
      <strong aria-live="polite">{head}</strong>
      {sub && <span>{sub}</span>}
    </div>
  )
}

function Progress({ job, onCancel }: { job: LocalJob; onCancel: () => void }) {
  const pct = job.total ? Math.min(100, Math.round((100 * job.bytes) / job.total)) : 0
  const phase = job.phase === 'verifying' ? 'Verifying'
    : job.phase === 'queued' ? 'Queued' : job.kind === 'runtime' ? 'Fetching the runtime' : 'Downloading'
  return (
    <div className="local-progress">
      <div className="local-line">
        <strong aria-live="polite">{phase} {job.target.replace('local/', '')}…</strong>
        <span>
          {fmtBytes(job.bytes)} of {fmtBytes(job.total)}
          {job.rate_bps ? ` · ${fmtBytes(job.rate_bps)}/s` : ''}
          {job.eta_s != null ? ` · ${fmtEta(job.eta_s)} left` : ''}
          {' · cloud models work meanwhile'}
        </span>
      </div>
      <div className="local-bar" role="progressbar" aria-label="Download progress" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}>
        <span style={{ width: `${pct}%` }} />
      </div>
      <div className="local-actions">
        <button className="btn ghost" onClick={onCancel}>Cancel</button>
      </div>
    </div>
  )
}

const label = (local: LocalStatus, id: string | null) =>
  local.models.find((m) => m.id === id)?.label ?? (id ?? '').replace('local/', '')

const fmtEta = (s: number) =>
  s < 60 ? `${Math.round(s)}s` : s < 3600 ? `${Math.round(s / 60)} min` : `${(s / 3600).toFixed(1)} h`
