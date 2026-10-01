import { Ev, type Approval, type Ask, type CatalogModel, type DirEntry, type FilePreview, type Root, type Msg, type Plan, type Profile, type RouteExplain, type SettingsDoc, type Status, type ToolCard, type WireEvent } from './types'

/**
 * Turning the event stream into renderable state.
 *
 * The engine emits facts in order; this folds them into a transcript. The
 * only subtle part is streaming: `assistant.delta` arrives token by token
 * and must append to the message in flight, while `assistant.message`
 * settles it. We keep a `streaming` flag rather than rebuilding the list
 * on every token.
 */

export type SessionState = {
  id: string
  title: string
  /** First thing the user asked — how the chat switcher names this chat. */
  label?: string
  workspace: string
  createdAt?: number
  messages: Msg[]
  plan: Plan | null
  status: Status | null
  approval: Approval | null
  ask: Ask | null
  busy: boolean
  /** Stop clicked, engine has not yet confirmed the abort. */
  stopping: boolean
  hop: number
  /** "root|path" -> children, so several roots can be expanded at once. */
  tree: Record<string, DirEntry[]>
  /** Flat file list for the composer's @ picker; loaded on first use. */
  fileIndex: { files: string[]; truncated: boolean } | null
  file: FilePreview | null
  term: { lines: any[]; cwd: string; shell: string }
  journal: { text: string; path: string } | null
  progress: { tool: string | null; chars: number } | null
  error: string | null
}

export type AppState = {
  connection: 'connecting' | 'open' | 'closed'
  /** False until engine.ready lands. Until then we know nothing — in
   *  particular we do NOT know whether a key exists, so showing the key
   *  gate would flash a login screen at someone who is already signed in. */
  ready: boolean
  hasKey: boolean
  keyError: string | null
  sessions: Record<string, SessionState>
  order: string[]
  active: string | null
  /** The engine's own description of every setting, and its current value.
   *  Controls render from this, so the UI never hardcodes the option list. */
  settings: SettingsDoc | null
  catalog: CatalogModel[]
  roots: Root[]
  activeRoot: string | null
  routeExplain: RouteExplain | null
  profile: Profile | null
  /** Last key.result. `at` is what lets the UI react to a repeat
   *  of the same outcome — two successes in a row are two events. */
  keyResult: { ok: boolean; detail: string; cleared: boolean; at: number } | null
  configError: { key: string; message: string } | null
}

export const initialState: AppState = {
  connection: 'connecting',
  ready: false,
  hasKey: false,
  keyError: null,
  sessions: {},
  order: [],
  active: null,
  settings: null,
  catalog: [],
  roots: [],
  activeRoot: null,
  routeExplain: null,
  profile: null,
  keyResult: null,
  configError: null,
}

let uid = 0
const nid = () => `m${++uid}`
// provider/model -> model (matches the ControlBar helper)
const shortModel = (m: string) => (m && m.includes('/') ? m.split('/').slice(1).join('/') : (m || ''))

function blank(id: string, title: string, workspace: string,
               createdAt?: number): SessionState {
  return {
    id, title, workspace, createdAt, messages: [], plan: null, status: null,
    approval: null, ask: null, busy: false, stopping: false, hop: 0, progress: null, error: null,
    tree: {}, fileIndex: null, file: null, journal: null,
    term: { lines: [], cwd: workspace, shell: '' },
  }
}

export type Action =
  | { t: 'connection'; state: AppState['connection'] }
  | { t: 'event'; ev: WireEvent }
  | { t: 'activate'; id: string }
  | { t: 'localUser'; id: string; text: string; attachments: string[]; refs?: string[] }
  | { t: 'dismissConfigError' }
  | { t: 'clearAsk'; id: string; answers?: string[] }
  | { t: 'closeFile'; id: string }
  | { t: 'clearTerm'; id: string }
  | { t: 'clearKeyError' }
  | { t: 'stopping'; id: string }

export function reduce(state: AppState, action: Action): AppState {
  if (action.t === 'connection') return { ...state, connection: action.state }
  if (action.t === 'dismissConfigError') return { ...state, configError: null }
  if (action.t === 'clearKeyError') return { ...state, keyError: null }

  if (action.t === 'stopping') {
    // Optimistic: the button flips to "stopping" on the click. The engine
    // confirms with TURN_ABORTED (or the turn ends on its own), which clears it.
    return mutate(state, action.id, (s) => (s.busy ? { ...s, stopping: true } : s))
  }

  if (action.t === 'clearTerm') {
    return mutate(state, action.id, (s) => ({ ...s, term: { ...s.term, lines: [] } }))
  }

  if (action.t === 'closeFile') {
    return mutate(state, action.id, (s) => ({ ...s, file: null }))
  }

  if (action.t === 'clearAsk') {
    // Resolved optimistically: the engine has no "ask answered" event, and
    // waiting for the next turn event would leave the question live for
    // however long the model takes to respond.
    return mutate(state, action.id, (s) => {
      const i = lastIndex(s.messages, (m) => m.kind === 'ask' && !m.answered)
      const messages = [...s.messages]
      if (i >= 0) {
        const m = messages[i]
        if (m.kind === 'ask') {
          messages[i] = { ...m, answered: action.answers ?? ['(left to the model)'] }
        }
      }
      return { ...s, ask: null, messages }
    })
  }
  if (action.t === 'activate') return { ...state, active: action.id }

  if (action.t === 'localUser') {
    // Echo the user's own message immediately — waiting for the round trip
    // would make the app feel laggy for no reason.
    return mutate(state, action.id, (s) => ({
      ...s,
      label: s.label || action.text.replace(/\s+/g, ' ').slice(0, 60),
      messages: [...s.messages, {
        kind: 'user', id: nid(), text: action.text, attachments: action.attachments,
        refs: action.refs,
      }],
    }))
  }

  const { ev } = action
  const sid = ev.session
  const d = ev.data ?? {}

  switch (ev.type) {
    case Ev.ENGINE_READY: {
      // The engine keeps every session's state, so a client that connects
      // late — or reconnects after a dropped socket — must adopt what is
      // already running rather than showing an empty app. The transcript
      // itself is not replayed; the session list, workspace and status are.
      const sessions = { ...state.sessions }
      const order = [...state.order]
      const settings = d.settings ?? state.settings
      const catalog = d.catalog?.length ? d.catalog : state.catalog
      const profile = d.profile ?? state.profile
      for (const row of (d.sessions ?? [])) {
        if (!sessions[row.id]) {
          sessions[row.id] = blank(row.id, row.title ?? 'session', row.workspace ?? '',
                                   row.created_at)
          order.push(row.id)
        }
        // Seed status from the descriptor so a hydrated session shows its
        // model, mode and cost immediately, instead of rendering em-dashes
        // until the next status event happens to fire.
        sessions[row.id] = {
          ...sessions[row.id],
          busy: !!row.busy,
          label: row.label ?? sessions[row.id].label,
          status: sessions[row.id].status ?? {
            model: row.model, mode: row.mode, route_mode: 'off',
            session_cost: row.session_cost ?? 0, session_tokens: row.session_tokens ?? 0,
            workspace: row.workspace ?? '', busy: !!row.busy, servers: [],
          },
        }
      }
      // Prune ghosts: a session that closed (or an engine that restarted)
      // while the socket was down is absent from this descriptor. Keeping it
      // in the sidebar meant typing into it echoed locally and then nothing
      // happened — no error, looked hung. The engine's list is authoritative.
      const liveIds = new Set((d.sessions ?? []).map((r: any) => r.id))
      for (const id of Object.keys(sessions)) {
        if (!liveIds.has(id)) delete sessions[id]
      }
      const prunedOrder = order.filter((id) => sessions[id])
      const active = (state.active && sessions[state.active])
        ? state.active : (prunedOrder[0] ?? null)
      return {
        ...state, ready: true, hasKey: !!d.has_key, sessions, order: prunedOrder,
        settings, catalog, profile, active,
      }
    }

    case Ev.SETTINGS:
      return { ...state, settings: d as SettingsDoc, configError: null }

    case Ev.FOLDERS:
      return { ...state, roots: d.roots ?? [], activeRoot: d.active ?? state.activeRoot }

    case Ev.CATALOG:
      return { ...state, catalog: d.models ?? [] }

    case Ev.ROUTE_EXPLAIN:
      return { ...state, routeExplain: d as RouteExplain }

    case Ev.CONFIG_ERROR:
      return { ...state, configError: { key: d.key, message: d.message } }

    case Ev.KEY_RESULT:
      return {
        ...state,
        // A FAILED replacement must not log you out. The engine verifies a
        // new key BEFORE saving, so the old working key is still in force —
        // downgrading hasKey here threw a signed-in user to the full-screen
        // gate (which has no cancel) over a single typo. Only ok:true or an
        // explicit clear moves it.
        hasKey: d.ok ? true : (d.cleared ? false : state.hasKey),
        keyError: d.ok ? null : (d.detail ?? 'key rejected'),
        profile: d.profile ?? state.profile,
        keyResult: {
          ok: !!d.ok, detail: d.detail ?? '', cleared: !!d.cleared, at: ev.seq,
        },
      }

    case Ev.SESSION_STARTED: {
      if (!sid) return state
      const prior = state.sessions[sid]
      // `resumed` means this is the SAME conversation being re-announced so
      // the client can focus it — reopening a project you already have open.
      // Blanking it here is what made that read as "my chat disappeared".
      const s = (d.resumed && prior)
        ? { ...prior, title: d.title ?? prior.title, label: d.label ?? prior.label,
            workspace: d.workspace ?? prior.workspace }
        : { ...blank(sid, d.title ?? 'session', d.workspace ?? '', d.created_at),
            label: d.label ?? '' }
      return {
        ...state,
        sessions: { ...state.sessions, [sid]: s },
        order: state.order.includes(sid) ? state.order : [...state.order, sid],
        // Always focus it. This used to be `state.active ?? sid`, so opening
        // a second project left the first one active: the new project's chat
        // was never shown and everything typed went into the old thread,
        // which is what made every project look like one merged chat.
        active: sid,
      }
    }

    case Ev.SESSION_CLOSED: {
      if (!sid) return state
      const sessions = { ...state.sessions }
      delete sessions[sid]
      const order = state.order.filter((x) => x !== sid)
      return { ...state, sessions, order, active: state.active === sid ? (order[0] ?? null) : state.active }
    }
  }

  if (!sid || !state.sessions[sid]) return state

  return mutate(state, sid, (s) => {
    switch (ev.type) {
      case Ev.STATUS:
        return { ...s, status: d as Status, busy: !!d.busy }

      case Ev.HISTORY: {
        // Replay from the engine's own history — the UI transcript did not
        // survive the restart, the conversation did. Only hydrate an EMPTY
        // transcript: live messages must never be clobbered by a late reply.
        if (s.messages.length > 0) return s
        const messages: Msg[] = []
        for (const m of (d.messages ?? []) as any[]) {
          if (m.kind === 'user') messages.push({ kind: 'user', id: nid(), text: m.text })
          else if (m.kind === 'assistant') messages.push({ kind: 'assistant', id: nid(), text: m.text })
          else if (m.kind === 'tool') messages.push({
            kind: 'tool', id: nid(),
            card: { callId: nid(), name: m.name, summary: m.summary,
                    status: m.ok ? 'ok' : 'error' } as ToolCard,
          })
          else if (m.kind === 'notice') messages.push({
            kind: 'notice', id: nid(), level: 'info', text: m.text })
        }
        return { ...s, messages }
      }

      case Ev.CHAT_CLEARED:
        return {
          ...s, messages: [], plan: null, approval: null, ask: null,
          progress: null, error: null,
        }

      case Ev.JOURNAL:
        return { ...s, journal: { text: d.text ?? '', path: d.path ?? '' } }

      case Ev.TURN_STARTED:
        return {
          ...s, busy: true, stopping: false, hop: 0, error: null, progress: null,
          // The chat's name is the first thing asked of it. localUser sets
          // this for the composer path; TURN_STARTED covers prompts arriving
          // any other way (another window, a restored session's first turn).
          label: s.label || String(d.text ?? '').replace(/\s+/g, ' ').slice(0, 60),
        }

      case Ev.HOP_STARTED:
        return { ...s, hop: d.hop ?? s.hop }

      case Ev.ASSISTANT_DELTA: {
        const last = s.messages[s.messages.length - 1]
        if (last && last.kind === 'assistant' && last.streaming) {
          const messages = s.messages.slice(0, -1)
          messages.push({ ...last, text: last.text + d.text })
          return { ...s, messages, progress: null }
        }
        return {
          ...s, progress: null,
          messages: [...s.messages, { kind: 'assistant', id: nid(), text: d.text, streaming: true }],
        }
      }

      case Ev.ASSISTANT_PROGRESS:
        return { ...s, progress: { tool: d.tool ?? null, chars: d.chars ?? 0 } }

      case Ev.ASSISTANT_MESSAGE: {
        const last = s.messages[s.messages.length - 1]
        if (last && last.kind === 'assistant' && last.streaming) {
          const messages = s.messages.slice(0, -1)
          messages.push({ kind: 'assistant', id: last.id, text: d.text, model: d.model })
          return { ...s, messages, progress: null }
        }
        return {
          ...s, progress: null,
          messages: [...s.messages, { kind: 'assistant', id: nid(), text: d.text, model: d.model }],
        }
      }

      case Ev.TOOL_PROPOSED:
        return {
          ...s,
          approval: {
            token: d.token, callId: d.call_id, name: d.name, summary: d.summary,
            preview: d.preview, blockedReason: d.blocked_reason, mode: d.mode,
          },
        }

      case Ev.TOOL_DECIDED: {
        if (d.decision === 'deny') {
          return {
            ...s, approval: null,
            messages: [...s.messages, {
              kind: 'tool', id: nid(),
              card: { callId: d.call_id, name: d.name, summary: d.name, args: {}, status: 'denied' },
            }],
          }
        }
        return { ...s, approval: null }
      }

      case Ev.TOOL_STARTED:
        return {
          ...s,
          messages: [...s.messages, {
            kind: 'tool', id: nid(),
            card: {
              callId: d.call_id, name: d.name, summary: d.summary,
              args: d.args, status: 'running',
            },
          }],
        }

      case Ev.TOOL_RESULT:
        return patchCard(s, d.call_id, (c) => ({
          ...c, status: d.ok ? 'ok' : 'error', result: d.result, deduped: d.deduped,
        }))

      case Ev.TOOL_SKIPPED:
        return {
          ...s,
          messages: [...s.messages, {
            kind: 'tool', id: nid(),
            card: {
              callId: d.call_id, name: d.name, summary: d.name, args: {},
              status: 'skipped', reason: d.reason,
            },
          }],
        }

      case Ev.DIR_LISTING:
        return { ...s, tree: { ...s.tree, [`${d.root ?? ''}|${d.path ?? ''}`]: d.entries ?? [] } }


      case Ev.FILE_OP:
        // Success needs no message — the tree re-lists and you can see it.
        // A failure must say why, or a refused rename looks like a no-op.
        return d.ok ? s : {
          ...s,
          messages: [...s.messages, {
            kind: 'notice', id: nid(), level: 'error',
            text: `Couldn't ${d.op === 'create.entry' ? 'create' : d.op === 'rename.entry' ? 'rename' : 'delete'} ${d.path || 'that'}: ${d.error}`,
          }],
        }

      case Ev.TERM_READY:
        return { ...s, term: { ...s.term, cwd: d.cwd ?? s.term.cwd, shell: d.shell ?? '' } }

      case Ev.TERM_OUTPUT:
        // Bounded: a `find /` would otherwise grow this array without limit.
        return { ...s, term: { ...s.term, lines: [...s.term.lines, d].slice(-2000) } }

      case Ev.FILE_PREVIEW:
        return { ...s, file: d as FilePreview }

      case Ev.FILE_INDEX:
        return { ...s, fileIndex: { files: d.files ?? [], truncated: !!d.truncated } }

      case Ev.PLAN_UPDATED: {
        // The plan shows inline as well as in the rail, so progress is
        // visible in the flow of the conversation the way it was in the
        // CLI. A REVISED plan (different step titles) gets its own card;
        // a status change patches the card already there, rather than
        // spamming a new checklist on every update_step.
        const plan = d as Plan
        const titles = plan.steps.map((x) => x.title).join('\u0000')
        const idx = lastIndex(s.messages, (m) => m.kind === 'plan')
        const prev = idx >= 0 ? s.messages[idx] : null
        if (prev && prev.kind === 'plan' &&
            prev.plan.steps.map((x) => x.title).join('\u0000') === titles) {
          const messages = [...s.messages]
          messages[idx] = { ...prev, plan }
          return { ...s, plan, messages }
        }
        return {
          ...s, plan,
          messages: [...s.messages, { kind: 'plan', id: nid(), plan }],
        }
      }

      case Ev.ASK_USER: {
        // Inline in the transcript, not a modal: the answer becomes part of
        // the history instead of vanishing with the dialog.
        const ask = { token: d.token, questions: d.questions }
        return {
          ...s, ask,
          messages: [...s.messages, { kind: 'ask', id: nid(), ask }],
        }
      }

      case Ev.SERVER_STARTED:
        return {
          ...s,
          messages: [...s.messages, {
            kind: 'server', id: nid(), url: d.url, pid: d.pid, port: d.port, cmd: d.cmd,
          }],
        }

      case Ev.SERVER_STOPPED:
        // Was silently dropped: the transcript's server card kept its live
        // dot and clickable URL to a dead server. Mark the matching card
        // stopped so it reads as finished.
        return {
          ...s,
          messages: s.messages.map((m) =>
            m.kind === 'server' && m.pid === d.pid
              ? { ...m, stopped: true } : m),
        }

      case Ev.FILE_CHANGED: {
        // The agent wrote a file; refresh the containing directory so the
        // explorer shows it. Dropping this event meant the agent's writes
        // never appeared in the tree until you collapsed and re-expanded.
        const abs = String(d.path ?? '')
        const tree = { ...s.tree }
        let touched = false
        for (const key of Object.keys(tree)) {
          const [root, rel] = key.split('|')
          const dir = rel ? `${root}/${rel}` : root
          // Invalidate the listing of the file's own directory.
          const parent = abs.slice(0, abs.lastIndexOf('/'))
          if (parent === dir || parent === root) {
            delete tree[key]
            touched = true
          }
        }
        // The agent's write may be a NEW file — drop the cached @ index so
        // the next picker open fetches a fresh one.
        return touched || s.fileIndex ? { ...s, tree, fileIndex: null } : s
      }

      case Ev.MEMORY_NOTE:
        // Was invisible: the model would say "I've noted that" and nothing
        // showed. A quiet notice makes the action visible.
        return {
          ...s,
          messages: [...s.messages, {
            kind: 'notice', id: nid(), level: 'info',
            text: 'Saved a note to repo memory.',
            detail: String(d.note ?? '').slice(0, 200) || undefined,
          }],
        }

      case Ev.NOTICE:
      case Ev.COMPACTED:
      case Ev.ROUTED: {
        const text =
          ev.type === Ev.COMPACTED
            ? `Compacted history (${d.before_tok}→${d.after_tok} tokens) — ${d.reason}`
            : ev.type === Ev.ROUTED
              ? (d.escalated
                  ? `Escalated to ${shortModel(d.model)} — ${d.reason ?? 'the model was struggling'}`
                  : d.settled
                    ? `Settled back to ${shortModel(d.model)} after clean progress`
                    : `Routed to ${shortModel(d.model)} (${d.cohort}${d.difficulty && d.difficulty !== 'mid' ? ` · ${d.difficulty}` : ''})`)
              : d.message
        return {
          ...s,
          messages: [...s.messages, {
            kind: 'notice', id: nid(), level: d.level ?? 'info', text, detail: d.detail,
          }],
        }
      }

      case Ev.ERROR: {
        // Only a FATAL error tears down the turn (busy/approval/ask). A
        // non-fatal error (an unrelated shell/panel hiccup) must NOT drop a
        // pending approval — the engine stays parked on it, and clearing it
        // here left the dialog gone and the turn silently denied 15 min later.
        const base = d.fatal ? settleStreaming(s) : s
        if (!d.fatal) {
          return {
            ...base,
            messages: [...base.messages, {
              kind: 'notice', id: nid(), level: 'error', text: d.message, detail: d.detail,
            }],
          }
        }
        return {
          ...base, busy: false, stopping: false, approval: null, ask: null,
          error: d.message,
          messages: [...base.messages, {
            kind: 'notice', id: nid(), level: 'error', text: d.message, detail: d.detail,
          }],
        }
      }

      case Ev.TURN_ABORTED: {
        const base = settleStreaming(s)
        return {
          ...base, busy: false, stopping: false, approval: null, ask: null, progress: null,
          messages: [...base.messages, {
            kind: 'notice', id: nid(), level: 'warn', text: 'Turn stopped.',
          }],
        }
      }

      case Ev.TURN_FINISHED: {
        const base = settleStreaming(s)
        const plan = d.plan ?? base.plan
        const messages: Msg[] = [...base.messages, {
          kind: 'turn', id: nid(), cost: d.cost, tokens: d.tokens,
          elapsed: d.elapsed, model: d.model, hops: d.hops,
          // Carry the estimate flag onto the permanent record so a computed
          // cost is shown as ~ here too, not just in the live bar.
          estimated: !!d.cost_estimated,
        }]
        // The plan is the model's OWN bookkeeping, kept by calling
        // update_step. It can finish the work and simply stop ticking the
        // steps — which leaves the panel reading "2/8" next to a run that
        // actually did all eight, with nothing on screen explaining the gap.
        if (plan && !plan.complete && (plan.total ?? 0) > 0) {
          messages.push({
            kind: 'notice', id: nid(), level: 'warn',
            text: `Turn ended with the plan still at ${plan.done}/${plan.total}.`,
            detail: 'The model stopped calling update_step, so the remaining '
              + 'steps may well be done — the counter just was not updated. '
              + 'Check the files, or ask it to finish the plan.',
          })
        }
        return { ...base, busy: false, stopping: false, progress: null, plan, messages }
      }

      default:
        return s
    }
  })
}

// A stream aborted or errored mid-flight never sends ASSISTANT_MESSAGE, so
// the last bubble stays `streaming: true` — a caret that blinks forever, and
// worse, the NEXT turn's deltas append into it (one merged message). Any
// terminal event settles it: strip the flag off a trailing streaming bubble.
function settleStreaming(s: SessionState): SessionState {
  const last = s.messages[s.messages.length - 1]
  if (!last || last.kind !== 'assistant' || !last.streaming) return s
  const messages = s.messages.slice(0, -1)
  // Drop an empty aborted bubble entirely; otherwise keep the partial text.
  if (last.text) messages.push({ ...last, streaming: false })
  return { ...s, messages }
}

function mutate(state: AppState, sid: string, fn: (s: SessionState) => SessionState): AppState {
  const cur = state.sessions[sid]
  if (!cur) return state
  return { ...state, sessions: { ...state.sessions, [sid]: fn(cur) } }
}

function lastIndex<T>(arr: T[], pred: (x: T) => boolean): number {
  for (let i = arr.length - 1; i >= 0; i--) if (pred(arr[i])) return i
  return -1
}

function patchCard(s: SessionState, callId: string, fn: (c: ToolCard) => ToolCard): SessionState {
  const i = [...s.messages].reverse().findIndex(
    (m) => m.kind === 'tool' && m.card.callId === callId)
  if (i < 0) return s
  const idx = s.messages.length - 1 - i
  const m = s.messages[idx]
  if (m.kind !== 'tool') return s
  const messages = [...s.messages]
  messages[idx] = { ...m, card: fn(m.card) }
  return { ...s, messages }
}
