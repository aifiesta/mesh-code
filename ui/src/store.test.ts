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
    let s = fire(withLocal, Ev.SESSION_STARTED, { title: 'w', workspace: '/w', resumed: true }, 's1')
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


describe('policy changes and the streaming bubble', () => {
  const start = (s: AppState) => {
    let n = fire(s, Ev.SESSION_STARTED, { title: 'w', workspace: '/w' }, 's1')
    n = fire(n, Ev.ASSISTANT_DELTA, { text: "I've created" }, 's1')
    return n
  }
  it('drops the streaming bubble only when the engine says the reply is discarded', () => {
    let s = start(initialState)
    s = fire(s, Ev.POLICY_CHANGED, { tier: 'escalated', model: 'x/y', reason: 'weak', discard_reply: false }, 's1')
    expect(s.sessions.s1.messages).toHaveLength(1)
    s = fire(s, Ev.POLICY_CHANGED, { tier: 'escalated', model: 'x/y', reason: 'weak', discard_reply: true }, 's1')
    expect(s.sessions.s1.messages).toHaveLength(0)
  })
})


describe('history replay after a reload mid-turn', () => {
  const ready = () => fire(initialState, Ev.ENGINE_READY, {
    has_key: true, sessions: [{ id: 's1', title: 'w', workspace: '/w', busy: true }],
  })
  it('puts the replay in front of what streamed since the reload, without duplicate tool cards', () => {
    let s = ready()
    expect(s.sessions.s1.needsHistory).toBe(true)
    s = fire(s, Ev.TOOL_STARTED, { call_id: 'c2', name: 'write_file', summary: 'write a.py' }, 's1')
    s = fire(s, Ev.HISTORY, { messages: [
      { kind: 'user', text: 'build it' },
      { kind: 'tool', id: 'c1', name: 'run_bash', summary: 'ls', ok: true },
      { kind: 'tool', id: 'c2', name: 'write_file', summary: 'write a.py', ok: true },
    ] }, 's1')
    const kinds = s.sessions.s1.messages.map((m) => m.kind)
    expect(kinds[0]).toBe('user')
    expect(s.sessions.s1.messages.filter((m) => m.kind === 'tool')).toHaveLength(2)
    expect(s.sessions.s1.needsHistory).toBe(false)
    const again = fire(s, Ev.HISTORY, { messages: [{ kind: 'user', text: 'dup' }] }, 's1')
    expect(again.sessions.s1.messages).toHaveLength(s.sessions.s1.messages.length)
  })
  it('never replays into a session this window started', () => {
    let s = fire(initialState, Ev.SESSION_STARTED, { title: 'w', workspace: '/w' }, 's2')
    s = fire(s, Ev.HISTORY, { messages: [{ kind: 'user', text: 'x' }] }, 's2')
    expect(s.sessions.s2.messages).toHaveLength(0)
  })
})
