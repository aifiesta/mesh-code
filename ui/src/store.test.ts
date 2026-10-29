import { describe, expect, it } from 'vitest'
import { initialState, reduce, type AppState } from './store'
import { Ev, type LocalStatus, type WireEvent } from './types'

const ev = (type: string, data: any, session: string | null = null): WireEvent =>
  ({ v: 1, seq: 0, type, session, ts: 0, data })
const fire = (s: AppState, type: string, data: any, session: string | null = null) =>
  reduce(s, { t: 'event', ev: ev(type, data, session) })

const local: LocalStatus = {
  enabled: true, booted: true, hardware: null, probe_failed: false, recommended: null,
  runtime: { tag: null, variant: null, source: 'none' },
  models: [{ id: 'local/a', label: 'A', bytes: 10, ctx_max: 1, ctx: null, installed: false,
             fits: true, slow: false, recommended: true, job: null }],
  default_model: 'local/a',
  server: { state: 'stopped', model: null, port: null, ctx: null, pid: null, restarts: 0, error: null, started_at: null },
  jobs: [], needs_confirm: null, declined: false, restart_needed: false, boot_error: null,
}
const job = (phase: string, extra: any = {}) =>
  ({ id: 'j1', kind: 'model', target: 'local/a', phase, bytes: 5, total: 10, rate_bps: 0, eta_s: null, error: null, ...extra })

const withLocal = fire(initialState, Ev.LOCAL_STATUS, local)

describe('local layer in the store', () => {
  it('replaces the whole status on local.status', () => {
    expect(withLocal.local).toEqual(local)
  })

  it('adds, patches and removes a job by phase, mirrored on the model row', () => {
    let s = fire(withLocal, Ev.LOCAL_PROGRESS, job('downloading'))
    expect(s.local!.jobs).toHaveLength(1)
    expect(s.local!.models[0].job?.bytes).toBe(5)
    s = fire(s, Ev.LOCAL_PROGRESS, job('downloading', { bytes: 9 }))
    expect(s.local!.jobs[0].bytes).toBe(9)
    expect(s.local!.models[0].job?.bytes).toBe(9)
    s = fire(s, Ev.LOCAL_PROGRESS, job('done'))
    expect(s.local!.jobs).toHaveLength(0)
    expect(s.local!.models[0].job).toBeNull()
    expect(s.appNotices).toHaveLength(0)
  })

  it('turns a failed download into an app notice', () => {
    const s = fire(withLocal, Ev.LOCAL_PROGRESS, job('error', { error: 'ConnectionError: reset' }))
    expect(s.local!.jobs).toHaveLength(0)
    expect(s.appNotices.map((n) => n.text)).toEqual(['a download failed: ConnectionError: reset'])
  })

  it('keeps session-less notices out of transcripts so HISTORY can still hydrate', () => {
    let s = fire(withLocal, Ev.SESSION_STARTED, { title: 'w', workspace: '/w' }, 's1')
    s = fire(s, Ev.NOTICE, { level: 'info', message: 'moved to local-first' })
    s = fire(s, Ev.LOCAL_READY, { model: 'local/a', first: true })
    expect(s.sessions.s1.messages).toHaveLength(0)
    expect(s.appNotices).toHaveLength(2)
    s = fire(s, Ev.HISTORY, { messages: [{ kind: 'user', text: 'hi' }] }, 's1')
    expect(s.sessions.s1.messages.map((m) => m.kind)).toEqual(['user'])
    s = reduce(s, { t: 'dismissAppNotice', id: s.appNotices[0].id })
    expect(s.appNotices).toHaveLength(1)
  })
})
