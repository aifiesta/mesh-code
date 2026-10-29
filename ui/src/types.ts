// Mirror of engine/meshharness/protocol.py. Keep the two in step — the
// string literals are the wire contract, not decoration.

export const Ev = {
  SETTINGS: 'settings',
  CATALOG: 'catalog',
  DIR_LISTING: 'dir.listing',
  FILE_PREVIEW: 'file.preview',
  FILE_INDEX: 'file.index',
  FILE_OP: 'file.op',
  TERM_OUTPUT: 'term.output',
  TERM_READY: 'term.ready',
  FOLDERS: 'folders',
  CONFIG_ERROR: 'config.error',
  SESSION_STARTED: 'session.started',
  SESSION_CLOSED: 'session.closed',
  TURN_STARTED: 'turn.started',
  TURN_FINISHED: 'turn.finished',
  TURN_ABORTED: 'turn.aborted',
  HOP_STARTED: 'hop.started',
  ASSISTANT_DELTA: 'assistant.delta',
  ASSISTANT_MESSAGE: 'assistant.message',
  ASSISTANT_PROGRESS: 'assistant.progress',
  TOOL_PROPOSED: 'tool.proposed',
  TOOL_DECIDED: 'tool.decided',
  TOOL_STARTED: 'tool.started',
  TOOL_RESULT: 'tool.result',
  TOOL_SKIPPED: 'tool.skipped',
  TOOL_REPAIRED: 'tool.repaired',
  PLAN_UPDATED: 'plan.updated',
  SERVER_STARTED: 'server.started',
  SERVER_STOPPED: 'server.stopped',
  FILE_CHANGED: 'file.changed',
  MEMORY_NOTE: 'memory.note',
  ASK_USER: 'ask.user',
  STATUS: 'status',
  NOTICE: 'notice',
  ERROR: 'error',
  COMPACTED: 'compacted',
  POLICY_CHANGED: 'policy.changed',
  BUDGET_UPDATED: 'budget.updated',
  BUDGET_HOLD: 'budget.hold',
  ENGINE_READY: 'engine.ready',
  HISTORY: 'history',
  CHAT_CLEARED: 'chat.cleared',
  JOURNAL: 'journal',
  KEY_RESULT: 'key.result',
  LOCAL_STATUS: 'local.status',
  LOCAL_PROGRESS: 'local.progress',
  LOCAL_READY: 'local.ready',
} as const

export const Req = {
  PROMPT: 'prompt',
  INTERRUPT: 'interrupt',
  APPROVAL_RESPONSE: 'approval.response',
  ASK_RESPONSE: 'ask.response',
  SET_MODE: 'set.mode',
  ESCALATE: 'escalate',
  DEESCALATE: 'deescalate',
  BUDGET_OVERRIDE: 'budget.override',
  BUDGET_STATUS: 'budget.status',
  SET_MODEL: 'set.model',
  SET_STYLE: 'set.style',
  OPEN_WORKSPACE: 'open.workspace',
  ADD_FOLDER: 'add.folder',
  REMOVE_FOLDER: 'remove.folder',
  NEW_SESSION: 'new.session',
  NEW_CHAT: 'new.chat',
  GET_HISTORY: 'get.history',
  GET_JOURNAL: 'get.journal',
  CLOSE_SESSION: 'close.session',
  STOP_SERVER: 'stop.server',
  COMPACT: 'compact',
  SAVE_KEY: 'save.key',
  CLEAR_KEY: 'clear.key',
  SET_CONFIG: 'set.config',
  LIST_DIR: 'list.dir',
  READ_FILE: 'read.file',
  FIND_FILES: 'find.files',
  CREATE_ENTRY: 'create.entry',
  RENAME_ENTRY: 'rename.entry',
  DELETE_ENTRY: 'delete.entry',
  TERM_RUN: 'term.run',
  TERM_INTERRUPT: 'term.interrupt',
  TERM_CLOSE: 'term.close',
  LIST_MODELS: 'list.models',
  LOCAL_STATUS: 'local.status',
  LOCAL_INSTALL: 'local.install',
  LOCAL_CANCEL: 'local.cancel',
  LOCAL_DELETE: 'local.delete',
  LOCAL_SERVER: 'local.server',
  LOCAL_REFRESH: 'local.refresh',
  LOCAL_DECLINE: 'local.decline',
} as const

export type WireEvent = {
  v: number
  seq: number
  type: string
  session: string | null
  ts: number
  data: any
}

export type Mode = 'default' | 'accept-edits' | 'auto' | 'bypass'

export const MODES: { id: Mode; label: string; blurb: string }[] = [
  { id: 'default', label: 'Ask every time', blurb: 'Confirm every file write and command' },
  { id: 'accept-edits', label: 'Accept edits', blurb: 'Auto-approve writes inside this project' },
  { id: 'auto', label: 'Auto', blurb: 'Also auto-approve shell commands — anything writing outside this project still asks' },
  { id: 'bypass', label: 'Bypass', blurb: 'Approve everything — dangerous shapes still ask' },
]

export type PlanStep = { index: number; title: string; status: string }
export type Plan = {
  steps: PlanStep[]; summary: string; done: number; total: number; complete: boolean
}

export type ToolCard = {
  callId: string
  name: string
  summary: string
  args: any
  status: 'proposed' | 'running' | 'ok' | 'error' | 'denied' | 'skipped'
  result?: string
  preview?: any
  deduped?: boolean
  reason?: string
}

export type Msg =
  | { kind: 'user'; id: string; text: string; attachments?: string[]; refs?: string[] }
  | { kind: 'assistant'; id: string; text: string; streaming?: boolean; model?: string }
  | { kind: 'tool'; id: string; card: ToolCard }
  | { kind: 'notice'; id: string; level: string; text: string; detail?: any }
  | { kind: 'turn'; id: string; cost: number; tokens: number; elapsed: number; model: string; hops: number; estimated?: boolean; local?: boolean; escalated?: string | null }
  | { kind: 'server'; id: string; url: string; pid: number; port: number; cmd: string; stopped?: boolean }
  | { kind: 'plan'; id: string; plan: Plan }
  | { kind: 'ask'; id: string; ask: Ask; answered?: string[] }

export type Approval = {
  token: string; callId: string; name: string; summary: string
  preview: any; blockedReason?: string; mode: string
}

export type AskQuestion = {
  question: string; header?: string
  options?: { label: string; description?: string }[]
  multiSelect?: boolean
}
export type Ask = { token: string; questions: AskQuestion[] }

export type BudgetWindow = {
  spent: number; limit: number; pct: number; resets: number
  by_kind: Record<string, number>; override?: boolean
}
export type BudgetSnapshot = {
  month: BudgetWindow
  day: BudgetWindow
  key_cap: boolean
  held: 'day' | 'month' | 'key_cap' | null
  estimated_usd: number
}

export type PolicyInfo = {
  policy: 'local-first' | 'cloud-auto' | 'pinned'
  tier: 'local' | 'cloud_fallback' | 'cloud_auto' | 'pinned' | 'escalated'
  escalation_model: string | null
  audit_model: string | null
  /** The model an escalation is on, or will be on next turn. */
  escalated: string | null
  escalated_reason: string | null
  escalation_pending: boolean
}

export type Status = {
  model: string; mode: Mode; policy?: PolicyInfo; budget?: BudgetSnapshot | null; session_cost: number
  session_tokens: number; workspace: string; busy: boolean
  /** The turn IN FLIGHT: the model a hop actually ran on (the gateway or a
   *  local pin may differ from `model`), and the spend so far. Session
   *  totals only move when a turn ends. */
  turn_model?: string | null
  turn_cost?: number
  turn_tokens?: number
  turn_estimated?: boolean
  /** The last hop ran on this machine: nothing was billed. */
  turn_local?: boolean
  servers: { pid: number; port: number; url: string; cmd: string }[]
}

/** "75,512 tok" is wider than the pill; "75.5k" is not. */
export function fmtTokens(n: number): string {
  if (!n) return '0'
  if (n < 1000) return String(n)
  if (n < 1_000_000) return `${(n / 1000).toFixed(n < 10_000 ? 1 : 0)}k`
  return `${(n / 1_000_000).toFixed(2)}M`
}

// ---- settings surface, rendered from the engine's own schema -------------

export type SettingOption = { value: any; label: string; blurb?: string }

export type SettingSpec = {
  key: string
  category: string
  /** enum | bool | int | money | text | model | model_list | local_model */
  type: string
  label: string
  help?: string
  why?: string
  options?: SettingOption[]
  default?: any
  value?: any
  min?: number
  max?: number
  beta?: boolean
  multiline?: boolean
  zero_label?: string
  depends_on?: Record<string, any>
}

export type SettingsDoc = {
  categories: { id: string; label: string; blurb: string }[]
  settings: SettingSpec[]
}

export type CatalogModel = {
  id: string
  name?: string
  /** A model served by this machine's llama-server (zero price). */
  local?: boolean
  installed?: boolean
  size_bytes?: number
  recommended?: boolean
  /** On the `local/auto` row: the concrete model it resolves to. */
  resolves_to?: string
  context_length?: number
  model_type?: string
  supports_thinking?: boolean
  input_modalities?: string[]
  output_modalities?: string[]
  is_free?: boolean
  pricing?: Record<string, number | string>
}

export type DirEntry = {
  name: string; dir: boolean; size: number
  noisy: boolean; hidden: boolean; path: string
}

export type FilePreview = {
  path: string; content: string; size?: number; lines?: number
  binary?: boolean; too_large?: boolean; error?: string
  /** Absolute root the path is relative to (set by the engine). */
  root?: string
}

export type Root = {
  path: string; name: string
  /** Set when this root IS an open session's workspace — the node doubles
   *  as the session: clicking it switches to that chat. */
  sessionId?: string
  busy?: boolean
}

export type SessionSummary = {
  id: string
  title: string
  workspace: string
  busy: boolean
  /** Unix seconds, from the engine. Used to group the list by day. */
  createdAt?: number
}

export type Profile = {
  signed_in: boolean
  key_hint: string
  key_source: string
  gateway: string
  version: string
  config_dir: string
  models: number
  /** What THIS APP has spent. Not an account balance — the gateway exposes
   *  none to a data-plane key, so this is a local tally, labelled as one. */
  lifetime?: { cost: number; tokens: number; turns: number; since: number | null }
  account_url?: string
  local?: { default_model: string | null; server_state: string; ram_gb: number | null }
}

export type LocalJob = {
  id: string; kind: 'model' | 'runtime'; target: string
  phase: 'queued' | 'downloading' | 'verifying' | 'done' | 'error' | 'cancelled'
  bytes: number; total: number; rate_bps: number; eta_s: number | null; error: string | null
}

export type LocalModelRow = {
  id: string; label: string; bytes: number; ctx_max: number; ctx: number | null
  installed: boolean; fits: boolean; slow: boolean; tight?: boolean; recommended: boolean
  family?: string; license?: string; job: LocalJob | null
}

export type LocalStatus = {
  enabled: boolean
  booted: boolean
  hardware: {
    os: string; arch: string; ram_gb: number; avail_gb: number; cores: number
    gpu: { kind: string; name: string; vram_gb: number; driver: string }
    disk_free_gb: number; probe_failed: boolean
  } | null
  probe_failed: boolean
  recommended: { model: string; ctx: number; reason: string; slow: boolean } | null
  runtime: { tag: string | null; variant: string | null; source: string; path?: string | null; error?: string | null }
  models: LocalModelRow[]
  default_model: string | null
  server: { state: string; model: string | null; port: number | null; ctx: number | null
            pid: number | null; restarts: number; error: string | null; started_at: number | null }
  jobs: LocalJob[]
  needs_confirm: { model: string; bytes: number; reason: 'size' | 'slow' } | null
  declined: boolean
  restart_needed: boolean
  boot_error: string | null
}

export const fmtBytes = (n: number) =>
  n >= 1e9 ? `${(n / 1e9).toFixed(n >= 1e10 ? 0 : 1)} GB`
    : n >= 1e6 ? `${Math.round(n / 1e6)} MB`
      : n >= 1e3 ? `${Math.round(n / 1e3)} KB` : `${n} B`

/** $/1M tokens for a catalog row, preferring discounted then per-1M then per-1k. */
export function pricePerM(m: CatalogModel, kind: 'prompt' | 'completion'): number | null {
  if (m.is_free) return 0
  const p = m.pricing ?? {}
  for (const [key, mult] of [
    [`${kind}_usd_per_1m_discounted`, 1],
    [`${kind}_usd_per_1m`, 1],
    [`${kind}_usd_per_1k`, 1000],
  ] as [string, number][]) {
    const v = p[key]
    if (v !== undefined && v !== null) {
      const n = Number(v)
      if (Number.isFinite(n)) return n * mult
    }
  }
  return null
}

export const fmtUsd = (n: number | null) =>
  n === null ? '—' : n === 0 ? 'free'
    : n < 1 ? `$${n.toFixed(3)}` : `$${n.toFixed(2)}`

export const fmtCtx = (n?: number) =>
  !n ? '—' : n >= 1000 ? `${Math.round(n / 1000)}k` : String(n)

/** Left-truncate a path so the filename always survives. Replaces a CSS
 *  `direction: rtl` trick that moved the leading "/" to the end. */
export function shortPath(p: string, keep = 3): string {
  if (!p) return ''
  const parts = p.split('/').filter(Boolean)
  if (parts.length <= keep) return p.startsWith('/') ? '/' + parts.join('/') : parts.join('/')
  return '…/' + parts.slice(-keep).join('/')
}
