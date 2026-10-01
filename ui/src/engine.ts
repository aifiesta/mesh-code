import { Req, type WireEvent } from './types'

/**
 * Connection to the local engine.
 *
 * The engine hands its port and token to the Electron main process on
 * stdout; main passes them to the window as query params. In `npm run dev`
 * there is no Electron, so we fall back to env-provided values.
 *
 * Reconnects with backoff. That matters more than it looks: the engine
 * keeps every session's state, so a dropped socket is a cosmetic event —
 * we resubscribe and carry on rather than losing the conversation.
 */
export class EngineClient {
  private ws: WebSocket | null = null
  private queue: string[] = []
  private retry = 0
  private closed = false
  private readonly url: string
  private readonly http: string
  private readonly token: string

  constructor(
    private onEvent: (ev: WireEvent) => void,
    private onConnection: (state: 'connecting' | 'open' | 'closed') => void,
  ) {
    const params = new URLSearchParams(location.search)
    const port = params.get('port') ?? import.meta.env.VITE_ENGINE_PORT ?? '8765'
    const token = params.get('token') ?? import.meta.env.VITE_ENGINE_TOKEN ?? ''
    this.token = token
    this.http = `http://127.0.0.1:${port}`
    this.url = `ws://127.0.0.1:${port}/ws?token=${encodeURIComponent(token)}`
    this.connect()
  }

  private connect() {
    if (this.closed) return
    this.onConnection('connecting')
    const ws = new WebSocket(this.url)
    this.ws = ws

    ws.onopen = () => {
      this.retry = 0
      this.onConnection('open')
      for (const m of this.queue.splice(0)) ws.send(m)
    }
    ws.onmessage = (e) => {
      try {
        this.onEvent(JSON.parse(e.data))
      } catch {
        /* a malformed frame must not kill the socket */
      }
    }
    ws.onclose = () => {
      this.onConnection('closed')
      if (this.closed) return
      const wait = Math.min(500 * 2 ** this.retry++, 8000)
      setTimeout(() => this.connect(), wait)
    }
    ws.onerror = () => ws.close()
  }

  send(type: string, payload: Record<string, unknown> = {}) {
    const msg = JSON.stringify({ type, ...payload })
    if (this.ws?.readyState === WebSocket.OPEN) this.ws.send(msg)
    else this.queue.push(msg)
  }

  dispose() {
    this.closed = true
    this.ws?.close()
  }

  // --- the requests the UI actually makes -----------------------------
  openWorkspace(workspace: string, mode = 'default') {
    this.send(Req.OPEN_WORKSPACE, { workspace, mode })
  }
  /** URL the viewer can put in an <iframe>/<img> for a workspace file. */
  fileUrl(session: string, root: string, path: string) {
    const b64 = btoa(unescape(encodeURIComponent(root)))
      .replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')
    const rel = path.split('/').map(encodeURIComponent).join('/')
    return `${this.http}/raw/${encodeURIComponent(this.token)}/${session}/${b64}/${rel}`
  }
  prompt(session: string, text: string, attachments: unknown[] = [], refs: string[] = []) {
    this.send(Req.PROMPT, { session, text, attachments, refs })
  }
  interrupt(session: string) {
    this.send(Req.INTERRUPT, { session })
  }
  approve(session: string, token: string, decision: 'allow' | 'deny' | 'always') {
    this.send(Req.APPROVAL_RESPONSE, { session, token, decision })
  }
  answer(session: string, token: string, answers: unknown) {
    this.send(Req.ASK_RESPONSE, { session, token, answers })
  }
  setMode(session: string, mode: string) {
    this.send(Req.SET_MODE, { session, mode })
  }
  setModel(session: string, model: string) {
    this.send(Req.SET_MODEL, { session, model })
  }
  setRoute(session: string, route_mode: string) {
    this.send(Req.SET_ROUTE, { session, route_mode })
  }
  stopServer(session: string, pid: number) {
    this.send(Req.STOP_SERVER, { session, pid })
  }
  compact(session: string) {
    this.send(Req.COMPACT, { session })
  }
  saveKey(key: string) {
    this.send(Req.SAVE_KEY, { key })
  }

  /** Beta feedback → the engine relays it to the Google Form (POST
   *  /api/feedback). Plain HTTP rather than the socket: a one-shot
   *  request wants a status code back, not a protocol event. */
  async postFeedback(rating: number, good: string, bad: string): Promise<boolean> {
    try {
      const r = await fetch(`${this.http}/api/feedback?token=${encodeURIComponent(this.token)}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ rating, good, bad }),
      })
      return r.ok
    } catch {
      return false
    }
  }
}
