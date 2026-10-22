import type { LocalStatus } from '../types'
import { fmtBytes, fmtCtx } from '../types'

/** Settings › Local model: machine, runtime, every manifest model and the
 *  server. The plain `local_*` fields render underneath from the schema. */
export function LocalModelPanel({ local, onInstall, onCancel, onDelete, onSetDefault, onServer, onRefresh }: {
  local: LocalStatus
  onInstall: (model: string) => void
  onCancel: (job: string) => void
  onDelete: (model: string) => void
  onSetDefault: (model: string) => void
  onServer: (action: 'stop' | 'restart') => void
  onRefresh: () => void
}) {
  const hw = local.hardware
  const srv = local.server
  const running = srv.state === 'ready' || srv.state === 'starting'
  // The engine refuses to delete this one until the server is stopped.
  const loaded = srv.model && srv.state !== 'stopped' ? srv.model : null

  return (
    <div className="localpanel">
      <div className="profile-head">This machine</div>
      {!local.enabled ? <p className="muted">Local model is off. Turn it on below to code without the cloud.</p>
      : local.boot_error ? <p className="field-dep">{local.boot_error}</p>
      : hw ? (
        <>
          <Row label="Memory" value={`${hw.ram_gb} GB`} />
          <Row label="GPU" value={hw.gpu.kind === 'none' ? 'None detected' : `${hw.gpu.kind}${hw.gpu.name ? ` · ${hw.gpu.name}` : ''}${hw.gpu.vram_gb ? ` · ${hw.gpu.vram_gb} GB` : ''}`} />
          <Row label="Free disk" value={`${hw.disk_free_gb} GB`} />
          {local.recommended && <Row label="Recommended" value={local.recommended.reason} />}
        </>
      ) : <p className="muted">{local.probe_failed ? 'The memory probe failed; pick a model by hand.' : 'Detecting…'}</p>}

      <div className="profile-head">Runtime</div>
      <Row label="llama-server" value={local.runtime.tag ? `${local.runtime.tag} · ${local.runtime.variant} · ${local.runtime.source}` : '—'} mono />
      {local.runtime.error && <p className="field-dep">{local.runtime.error}</p>}

      <div className="profile-head">
        Models
        <button className="link" onClick={onRefresh} title="Check for newer pinned models">Refresh list</button>
      </div>
      <div className="browser-head">
        <span>Model</span><span className="num">Size</span><span className="num">Context</span><span />
      </div>
      <div className="browser-rows">
        {local.models.map((m) => {
          const isDefault = m.id === local.default_model
          const job = local.jobs.find((j) => j.target === m.id) ?? m.job
          const inUse = loaded === m.id
          return (
            <div className={`browser-row ${isDefault ? 'current' : ''} ${!m.fits ? 'off' : ''}`} key={m.id}>
              <span className="m-id" title={m.id}>
                {m.label}
                {m.recommended && <span className="tag now">recommended</span>}
                {m.installed && <span className="tag think">installed</span>}
                {m.slow && <span className="tag beta">slow here</span>}
                {!m.fits && <span className="tag never">too big</span>}
              </span>
              <span className="num">{fmtBytes(m.bytes)}</span>
              <span className="m-ctx num">{fmtCtx(m.ctx ?? m.ctx_max)}</span>
              <span className="m-actions">
                {job
                  ? <button onClick={() => onCancel(job.id)}>Cancel {Math.round((100 * job.bytes) / Math.max(job.total, 1))}%</button>
                  : m.installed
                    ? <>
                        {!isDefault && <button className="use" onClick={() => onSetDefault(m.id)}>Make default</button>}
                        <button className="danger" disabled={inUse} title={inUse ? 'Stop the server first' : undefined} onClick={() => onDelete(m.id)}>Delete</button>
                      </>
                    : <button className="use" disabled={!m.fits} onClick={() => onInstall(m.id)}>Install</button>}
              </span>
            </div>
          )
        })}
      </div>

      <div className="profile-head">Server</div>
      <Row label="State" value={srv.state + (local.restart_needed ? ' · restart to apply new settings' : '')} />
      {srv.model && <Row label="Running" value={`${srv.model.replace('local/', '')} · ${fmtCtx(srv.ctx ?? undefined)} context`} mono />}
      {srv.error && <p className="field-dep">{srv.error}</p>}
      <div className="local-actions">
        <button className="btn subtle" disabled={!running} onClick={() => onServer('stop')}>Stop</button>
        <button className="btn subtle" disabled={!local.default_model} onClick={() => onServer('restart')}>Restart</button>
      </div>
    </div>
  )
}

function Row({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="profile-row">
      <span>{label}</span>
      <span className={mono ? 'mono' : ''} title={value}>{value}</span>
    </div>
  )
}
