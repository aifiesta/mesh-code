import { useCallback, useEffect, useReducer, useRef, useState } from 'react'
import { EngineClient } from './engine'
import { initialState, reduce, type SessionState } from './store'
import { dismissOverlays } from './useDismissable'
import { MODES, Req, shortPath, type LocalStatus, type Mode, type Profile } from './types'
import { ApprovalDialog } from './components/ApprovalDialog'
import { CommandPalette, type Action } from './components/CommandPalette'
import { ControlBar } from './components/ControlBar'
import { SettingsSheet } from './components/SettingsSheet'
import { Wordmark } from './components/Logo'
import { PlanCard } from './components/PlanCard'
import { Sidebar } from './components/Sidebar'
import { Splash } from './components/Splash'
import { TerminalPanel } from './components/TerminalPanel'
import { Tour } from './components/Tour'
import { Thinking } from './components/Thinking'
import { AskInline } from './components/AskInline'
import { Composer } from './components/Composer'
import { KeyGate } from './components/KeyGate'
import { LocalSetupCard } from './components/LocalSetupCard'
import { Markdown } from './components/Markdown'
import { Chevron } from './components/Chevron'
import { FileBadge } from './components/FileBadge'
import { Explorer } from './components/Explorer'
import { Rail } from './components/Rail'
import { ToolCardView } from './components/ToolCardView'

export default function App() {
  const [state, dispatch] = useReducer(reduce, initialState)
  const clientRef = useRef<EngineClient | null>(null)
  const [sheet, setSheet] = useState<string | null>(null)
  const [palette, setPalette] = useState(false)
  const [localOnly, setLocalOnly] = useState<boolean>(() => {
    try { return localStorage.getItem('mesh.localOnly') === '1' } catch { return false }
  })
  const [keyDialog, setKeyDialog] = useState(false)
  const localInstalled = !!state.local?.enabled && state.local.models.some((m) => m.installed)
  const gateShown = state.ready && !state.hasKey && !(localOnly && localInstalled)
  const gateRef = useRef(gateShown)
  gateRef.current = gateShown
  const [confirmDelete, setConfirmDelete] = useState<any>(null)
  // Switching the active repository is a bigger move than it looks: the
  // conversation you are leaving keeps running, and people assumed it was
  // being thrown away. Confirm it, and say plainly what happens.
  const [confirmSwitch, setConfirmSwitch] = useState<{ path: string; name: string } | null>(null)
  const [confirmClose, setConfirmClose] = useState<string | null>(null)
  // The rail is 300px of permanent chrome; on a laptop that is a third of
  // the transcript. Hiding it is remembered per machine.
  // The left sidebar collapses too (⌘B, or the chevron in its header) —
  // on a laptop, chat plus a file in the rail wants every pixel.
  const [sideOpen, setSideOpen] = useState(() => {
    try { return localStorage.getItem('mesh.side') !== 'hidden' } catch { return true }
  })
  useEffect(() => {
    try { localStorage.setItem('mesh.side', sideOpen ? 'open' : 'hidden') } catch { /* ignore */ }
  }, [sideOpen])
  const [railOpen, setRailOpen] = useState(() => {
    try { return localStorage.getItem('mesh.rail') !== 'hidden' } catch { return true }
  })
  useEffect(() => {
    try { localStorage.setItem('mesh.rail', railOpen ? 'open' : 'hidden') } catch { /* ignore */ }
  }, [railOpen])
  // Never self-starting. It is offered by a card in the sidebar and by the
  // palette; an app that seizes the screen with a walkthrough the moment it
  // opens is doing it TO you. `tourTaken` only decides how loudly the card
  // advertises itself.
  const [tour, setTour] = useState(false)
  const [tourTaken, setTourTaken] = useState(() => {
    try { return !!localStorage.getItem('mesh.tour.v1') } catch { return true }
  })
  // setConfig is a stable callback shared with the palette; a ref keeps it
  // pointed at the live session without re-creating it on every switch.
  const activeRef = useRef<string | null>(null)
  // Same reason as activeRef: stable callbacks need the live state
  // without taking it as a dependency.
  const stateRef = useRef(state)
  stateRef.current = state

  // `system` stamps no attribute and lets prefers-color-scheme win. The
  // storage key is versioned so "never chose" and "chose system" differ.
  const [theme, setTheme] = useState<'system' | 'light' | 'dark'>(() => {
    try { return (localStorage.getItem('mesh.theme.v2') as any) || 'light' }
    catch { return 'light' }
  })

  useEffect(() => {
    const root = document.documentElement
    if (theme === 'system') root.removeAttribute('data-theme')
    else root.setAttribute('data-theme', theme)
    try { localStorage.setItem('mesh.theme.v2', theme) } catch { /* private mode */ }
    // Tell Electron too: the window's native vibrancy is painted by the OS,
    // not by our CSS, so without this the frame stays dark under a light UI.
    ;(window as any).meshHarness?.setTheme?.(theme)
  }, [theme])

  // Sidebar width lives in a CSS variable so dragging is a style write, not
  // a React re-render on every mousemove. Persisted per machine.
  const [sideW, setSideW] = useState(() => {
    try { return Number(localStorage.getItem('mesh.sidebar') || 232) || 232 } catch { return 232 }
  })
  const dragging = useRef(false)
  const [sideDragging, setSideDragging] = useState(false)
  // The rail holds a live preview and a shell — both of which people want
  // wider than the 300px it was nailed to. Same mechanism as the sidebar:
  // a CSS variable so dragging never re-renders React.
  const [railW, setRailW] = useState(() => {
    try { return Number(localStorage.getItem('mesh.railw') || 300) || 300 } catch { return 300 }
  })
  const railDragging = useRef(false)
  const [railDrag, setRailDrag] = useState(false)

  useEffect(() => {
    document.documentElement.style.setProperty('--side-w', `${sideW}px`)
    try { localStorage.setItem('mesh.sidebar', String(sideW)) } catch { /* private mode */ }
  }, [sideW])

  useEffect(() => {
    document.documentElement.style.setProperty('--rail-w', `${railW}px`)
    try { localStorage.setItem('mesh.railw', String(railW)) } catch { /* private mode */ }
  }, [railW])

  const startResize = useCallback((e: React.PointerEvent) => {
    e.preventDefault()
    dragging.current = true
    setSideDragging(true)
    document.body.classList.add('resizing')
    const move = (ev: PointerEvent) => {
      if (!dragging.current) return
      // Clamp: narrower than 176 truncates project names to uselessness,
      // wider than 420 just steals room from the transcript.
      setSideW(Math.max(176, Math.min(420, ev.clientX)))
    }
    const up = () => {
      dragging.current = false
      setSideDragging(false)
      document.body.classList.remove('resizing')
      window.removeEventListener('pointermove', move)
      window.removeEventListener('pointerup', up)
    }
    window.addEventListener('pointermove', move)
    window.addEventListener('pointerup', up)
  }, [])

  // The rail is anchored to the right edge, so its width grows as the
  // pointer moves LEFT — measured from the window edge, not from 0.
  const startRailResize = useCallback((e: React.PointerEvent) => {
    e.preventDefault()
    railDragging.current = true
    setRailDrag(true)
    document.body.classList.add('resizing')
    const move = (ev: PointerEvent) => {
      if (!railDragging.current) return
      // Under 240 the preview iframe and the shell stop being readable;
      // over 620 the transcript is the one being squeezed instead.
      setRailW(Math.round(Math.max(240, Math.min(620, window.innerWidth - ev.clientX))))
    }
    const up = () => {
      railDragging.current = false
      setRailDrag(false)
      document.body.classList.remove('resizing')
      window.removeEventListener('pointermove', move)
      window.removeEventListener('pointerup', up)
    }
    window.addEventListener('pointermove', move)
    window.addEventListener('pointerup', up)
  }, [])

  useEffect(() => {
    const c = new EngineClient(
      (ev) => dispatch({ t: 'event', ev }),
      (s) => dispatch({ t: 'connection', state: s }),
    )
    clientRef.current = c
    return () => c.dispose()
  }, [])

  // The native File > Open Project… menu item routes through the preload
  // bridge; wire it to the same handler the sidebar button uses.
  useEffect(() => {
    const bridge = (window as any).meshHarness
    return bridge?.onOpenWorkspace?.((dir: string) => {
      clientRef.current?.openWorkspace(dir)
    })
  }, [])

  // A modal claiming the screen closes every popover and menu underneath it.
  // Without this they simply stacked — palette over account menu over mode
  // menu over a control popover, all visible at once.
  useEffect(() => {
    if (palette || sheet || keyDialog || tour) dismissOverlays()
  }, [palette, sheet, keyDialog, tour])

  // Close the key dialog once the engine confirms. Sign-out closes too —
  // it may legitimately end with no key at all, which is not an error.
  const lastKeyAt = useRef(0)
  useEffect(() => {
    const r = state.keyResult
    if (!r || r.at === lastKeyAt.current) return
    lastKeyAt.current = r.at
    if (r.ok || r.cleared) setKeyDialog(false)
  }, [state.keyResult])

  // The two confirm dialogs had no Escape handling; every other modal
  // closes on it. One effect dismisses whichever is open.
  useEffect(() => {
    if (!confirmDelete && !confirmSwitch && !confirmClose) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') { setConfirmDelete(null); setConfirmSwitch(null); setConfirmClose(null) }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [confirmDelete, confirmSwitch, confirmClose])

  // Opening the key dialog clears any stale rejection first, so reopening
  // "Replace API key" never greets a pristine form with the old red error.
  const openKeyDialog = useCallback(() => {
    dispatch({ t: 'clearKeyError' })
    setKeyDialog(true)
  }, [])

  const endTour = useCallback(() => {
    setTour(false)
    setTourTaken(true)
    try { localStorage.setItem('mesh.tour.v1', 'done') } catch { /* ignore */ }
  }, [])

  const client = clientRef.current
  const session: SessionState | null = state.active ? state.sessions[state.active] ?? null : null
  activeRef.current = state.active

  // A restored session arrives with its conversation in the ENGINE but an
  // empty UI transcript — request the replay once per session.
  const hydrated = useRef<Set<string>>(new Set())
  useEffect(() => {
    if (!session || session.busy) return
    if (session.messages.length > 0) { hydrated.current.add(session.id); return }
    if (hydrated.current.has(session.id)) return
    hydrated.current.add(session.id)
    clientRef.current?.send(Req.GET_HISTORY, { session: session.id })
  }, [session])

  // Files created by SHELL COMMANDS never emit FILE_CHANGED (only write_file
  // does), so the tree the explorer cached at open time goes stale the moment
  // the agent runs `npm create …` — a whole app got built while the sidebar
  // still said "Empty folder". Re-request every listing we're showing when a
  // turn ends. Bounded, active session only.
  const wasBusy = useRef(false)
  useEffect(() => {
    const busy = !!session?.busy
    if (wasBusy.current && !busy && session) {
      for (const key of Object.keys(session.tree).slice(0, 40)) {
        const cut = key.indexOf('|')
        const root = key.slice(0, cut)
        const path = key.slice(cut + 1)
        if (root) clientRef.current?.send(Req.LIST_DIR, { session: session.id, root, path })
      }
    }
    wasBusy.current = busy
  }, [session?.busy, session])

  // ⌘K opens the palette, ⌘, the settings sheet — the two conventions a
  // desktop user already has, so neither surface needs to be advertised.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const mod = e.metaKey || e.ctrlKey
      // A pending approval owns the screen. Opening the palette or sheet
      // under its scrim let ⌘K's autofocus steal the caret — you'd type into
      // an invisible palette and Enter could run a hidden action.
      if (activeRef.current && stateRef.current.sessions[activeRef.current]?.approval) return
      if (gateRef.current) return
      if (mod && e.key.toLowerCase() === 'k') { e.preventDefault(); setPalette((p) => !p) }
      if (mod && e.key === ',') { e.preventDefault(); setSheet((v) => (v ? null : 'model')) }
      if (mod && e.key.toLowerCase() === 'b') { e.preventDefault(); setSideOpen((v) => !v) }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  // Stable callbacks: Explorer runs onList inside an effect, so a fresh
  // identity each render would re-request the root directory forever —
  // the same trap the settings sheet's catalog refresh fell into.
  const listDir = useCallback((root: string, path: string) => {
    if (!activeRef.current) return
    clientRef.current?.send(Req.LIST_DIR, { session: activeRef.current, root, path })
  }, [])

  const addFolder = useCallback(async () => {
    const bridge = (window as any).meshHarness
    const dir = bridge?.pickFolder
      ? await bridge.pickFolder()
      : window.prompt('Folder to add', '~/code')
    if (dir) clientRef.current?.send(Req.ADD_FOLDER, { path: dir })
  }, [])

  const mentionPath = useCallback((path: string) => {
    window.dispatchEvent(new CustomEvent('mesh:mention', { detail: path }))
  }, [])

  const setConfig = useCallback((key: string, value: any) => {
    clientRef.current?.send(Req.SET_CONFIG, { session: activeRef.current, key, value })
  }, [])

  // Ask before leaving a conversation that has something in it. An empty
  // session has nothing to lose, so switching out of one is immediate —
  // a confirmation nobody can answer wrongly is just a click tax.
  const requestSwitch = useCallback((path: string, name: string) => {
    const cur = activeRef.current ? stateRef.current.sessions[activeRef.current] : null
    const worth = cur && cur.workspace !== path && (cur.busy || cur.messages.length > 0)
    if (worth) setConfirmSwitch({ path, name })
    else clientRef.current?.openWorkspace(path)
  }, [])

  const openFolder = useCallback(async () => {
    // Electron exposes a native folder picker on the preload bridge. In a
    // plain browser (npm run dev) fall back to typing a path.
    const bridge = (window as any).meshHarness
    const dir = bridge?.pickFolder
      ? await bridge.pickFolder()
      : window.prompt('Project folder', '~/code')
    if (dir) requestSwitch(dir, dir.replace(/\/+$/, '').split('/').pop() || dir)
  }, [requestSwitch])

  // The gate retakes the screen with no overlay left behind it.
  useEffect(() => { if (gateShown) { setSheet(null); setPalette(false); setKeyDialog(false) } }, [gateShown])

  // local_model must name an installed model; a pick the engine reports as
  // not on disk is cleared.
  const localModelPick = state.settings?.settings.find((s) => s.key === 'local_model')?.value as string | undefined
  useEffect(() => {
    const row = localModelPick ? state.local?.models.find((m) => m.id === localModelPick) : undefined
    if (row && !row.installed && !row.job && !state.local?.jobs.some((j) => j.target === row.id)) setConfig('local_model', '')
  }, [localModelPick, state.local, setConfig])

  if (!state.ready) return <Splash connection={state.connection} />

  const localCard = state.local && (state.local.jobs.length > 0 || state.local.needs_confirm
    || (state.local.server.state === 'failed' && state.local.server.error)) ? state.local : null
  const appNotices = state.appNotices.map((n) => (
    <div className={`notice ${n.level} app-notice`} key={n.id}>
      {n.text}
      <button className="link" onClick={() => dispatch({ t: 'dismissAppNotice', id: n.id })}>Dismiss</button>
    </div>
  ))

  if (gateShown) {
    return (
      <KeyGate
        error={state.keyError}
        resultAt={state.keyResult?.at}
        onSave={(k) => client?.saveKey(k)}
        onContinueLocal={localInstalled ? () => {
          try { localStorage.setItem('mesh.localOnly', '1') } catch { /* private mode */ }
          setLocalOnly(true)
        } : undefined}
        below={
          <>
            {appNotices}
            {state.local && (
              <LocalSetupCard
                local={state.local}
                onInstall={(m) => client?.localInstall(m)}
                onCancel={(j) => client?.localCancel(j)}
                onDecline={() => client?.localDecline()}
              />
            )}
          </>
        }
      />
    )
  }

  return (
    <div className={`app ${sideOpen ? '' : 'side-hidden'}`}>
      {!sideOpen && (
        <button className="side-show" onClick={() => setSideOpen(true)}
                aria-label="Show sidebar" title="Show sidebar (⌘B)">
          <Chevron dir="right" />
        </button>
      )}
      {sideOpen && <Sidebar
        onHide={() => setSideOpen(false)}
        sessions={state.order.map((id) => state.sessions[id]).filter(Boolean).map((x) => ({
          id: x.id, title: x.title, workspace: x.workspace,
          busy: x.busy, createdAt: x.createdAt,
        }))}
        profile={state.profile}
        theme={theme}
        connection={state.connection}
        busyAnywhere={Object.values(state.sessions).some((x) => x.busy)}
        onNewSession={openFolder}
        onOpenFolder={openFolder}
        onBrowseModels={() => setSheet('model')}
        onSettings={() => setSheet('model')}
        onPalette={() => setPalette(true)}
        onTour={() => setTour(true)}
        tourTaken={tourTaken}
        onManageKey={openKeyDialog}
        onSetTheme={setTheme}
        onSubmitFeedback={(rating, good, bad) =>
          clientRef.current?.postFeedback(rating, good, bad) ?? Promise.resolve(false)}
        explorer={session ? (
          <>
            <Explorer
              /* The files panel follows the active session: its workspace is
                 the expanded root, other sessions' projects are reached by
                 clicking them above; only added browse folders sit alongside. */
              roots={(() => {
                // Every open session is a project NODE (the sessions list is
                // gone — these nodes are it), active first; then folders
                // added purely for browsing.
                // STABLE order (creation order) — the active project is
                // shown by highlight, never by moving it. ONE node per
                // WORKSPACE: a project can hold several chats, switched in
                // the chat header, so the node points at the active chat
                // when it lives here, else the project's most recent one.
                const byWs = new Map<string, { path: string; name: string; sessionId: string; busy: boolean }>()
                for (const id of state.order) {
                  const x = state.sessions[id]
                  if (!x) continue
                  const cur = byWs.get(x.workspace)
                  const mine = x.id === state.active
                  if (!cur || mine) {
                    byWs.set(x.workspace, { path: x.workspace, name: x.title,
                                            sessionId: x.id,
                                            busy: (cur?.busy ?? false) || x.busy })
                  } else if (x.busy) {
                    byWs.set(x.workspace, { ...cur, busy: true })
                  }
                }
                const nodes = [...byWs.values()]
                const taken = new Set(nodes.map((n) => n.path))
                const extras = state.roots.filter((r) => !taken.has(r.path))
                return [...nodes, ...extras]
              })()}
              activeRoot={session.workspace}
              listing={session.tree}
              activePath={session.file?.path ?? null}
              onList={listDir}
              onOpen={(root, p) => client?.send(Req.READ_FILE,
                { session: session.id, root, path: p })}
              onMention={mentionPath}
              onCreate={(root, p, kind) => client?.send(Req.CREATE_ENTRY,
                { session: session.id, root, path: p, kind })}
              onRename={(root, p, name) => client?.send(Req.RENAME_ENTRY,
                { session: session.id, root, path: p, name })}
              onDelete={(root, entry) => setConfirmDelete({ ...entry, root })}
              onClose={(root) => client?.send(Req.REMOVE_FOLDER, { path: root })}
              onMakeActive={requestSwitch}
              onActivateSession={(id) => dispatch({ t: 'activate', id })}
              chats={(() => {
                const by: Record<string, { id: string; label: string; createdAt?: number; busy: boolean }[]> = {}
                for (const id of state.order) {
                  const x = state.sessions[id]
                  if (!x) continue
                  ;(by[x.workspace] ??= []).push({
                    id: x.id, label: x.label || 'New chat',
                    createdAt: x.createdAt, busy: x.busy })
                }
                return by
              })()}
              activeChat={session.id}
              onSwitchChat={(id) => dispatch({ t: 'activate', id })}
              onCloseSession={setConfirmClose}
              onNewChat={(path) => client?.send(Req.NEW_SESSION, { workspace: path })}
            />
            <button className="add-folder" onClick={addFolder}>+ Add folder…</button>
          </>
        ) : null}
        onResize={startResize}
        resizing={sideDragging}
      />}

      {confirmDelete && session && (
        <div className="modal-scrim" onClick={() => setConfirmDelete(null)}>
          <div className="modal confirm" onClick={(e) => e.stopPropagation()} role="dialog" aria-modal="true">
            <header>
              <span className="tool-chip run_bash">delete</span>
              <h2>Delete “{confirmDelete.name}”?</h2>
            </header>
            <div className="approval-body">
              <p className="confirm-text">
                {confirmDelete.dir
                  ? 'This folder and everything inside it will be removed.'
                  : 'This file will be removed.'}{' '}
                <b>This is permanent — it does not go to the Trash.</b>
              </p>
              <pre className="command-block">{confirmDelete.path}</pre>
            </div>
            <footer>
              <button className="btn ghost" onClick={() => setConfirmDelete(null)}>Cancel</button>
              <div className="spacer" />
              <button className="btn danger-solid" onClick={() => {
                client?.send(Req.DELETE_ENTRY, {
                  session: session.id, root: confirmDelete.root, path: confirmDelete.path })
                setConfirmDelete(null)
              }}>Delete permanently</button>
            </footer>
          </div>
        </div>
      )}
      {confirmSwitch && (
        <div className="modal-scrim" onClick={() => setConfirmSwitch(null)}>
          <div className="modal confirm" onClick={(e) => e.stopPropagation()} role="dialog" aria-modal="true">
            <header>
              <span className="tool-chip read_file">switch</span>
              <h2>Open “{confirmSwitch.name}”?</h2>
            </header>
            <div className="approval-body">
              <p className="confirm-text">
                {session?.busy
                  ? <>“{session.title}” is still working. It keeps running in the background — </>
                  : <>“{session?.title}” stays open — </>}
                its conversation is kept and you can return to it from the sidebar at any time.
                Each project has its own chat.
              </p>
              <pre className="command-block">{confirmSwitch.path}</pre>
            </div>
            <footer>
              <button className="btn ghost" onClick={() => setConfirmSwitch(null)}>Cancel</button>
              <div className="spacer" />
              <button className="btn primary" onClick={() => {
                client?.openWorkspace(confirmSwitch.path)
                setConfirmSwitch(null)
              }}>Open project</button>
            </footer>
          </div>
        </div>
      )}
      {confirmClose && state.sessions[confirmClose] && (() => {
        const x = state.sessions[confirmClose]
        const last = !Object.values(state.sessions).some((o) => o.id !== x.id && o.workspace === x.workspace)
        return (
          <div className="modal-scrim" onClick={() => setConfirmClose(null)}>
            <div className="modal confirm" onClick={(e) => e.stopPropagation()} role="dialog" aria-modal="true">
              <header>
                <span className="tool-chip run_bash">close</span>
                <h2>Close “{x.label || x.title}”?</h2>
              </header>
              <div className="approval-body">
                <p className="confirm-text">
                  {x.busy ? <>It is still working; closing stops it. </> : null}
                  The conversation is removed and cannot be reopened.
                  {last ? <> This is the last chat in <b>{x.title}</b>, so the project leaves the sidebar too.</> : null}
                </p>
                <pre className="command-block">{x.workspace}</pre>
              </div>
              <footer>
                <button className="btn ghost" onClick={() => setConfirmClose(null)}>Keep it</button>
                <div className="spacer" />
                <button className="btn danger-solid" onClick={() => {
                  client?.send(Req.CLOSE_SESSION, { session: x.id })
                  // The project node exists for its chats: the last one closing takes it along.
                  if (last) client?.send(Req.REMOVE_FOLDER, { path: x.workspace })
                  setConfirmClose(null)
                }}>Close chat</button>
              </footer>
            </div>
          </div>
        )
      })()}
      {tour && <Tour onDone={endTour} />}
      {keyDialog && (
        <KeyGate
          variant="modal"
          error={state.keyError}
          resultAt={state.keyResult?.at}
          hint={state.profile?.key_hint}
          source={state.profile?.key_source}
          onSave={(k) => client?.saveKey(k)}
          onClear={() => client?.send(Req.CLEAR_KEY, {})}
          onClose={() => setKeyDialog(false)}
        />
      )}
      {palette && (
        <CommandPalette
          settings={state.settings}
          onSet={setConfig}
          onClose={() => setPalette(false)}
          extra={paletteActions(
            () => openFolder(),
            () => setSheet('model'),
            () => session && client?.compact(session.id),
            openKeyDialog,
            () => setTour(true),
            setTheme,
            (m: string) => session && client?.setMode(session.id, m),
            () => setSheet('local'),
            state.local?.default_model ? () => client?.localServer('restart') : null,
          )}
        />
      )}
      {sheet && (
        <SettingsSheet
          settings={state.settings}
          catalog={state.catalog}
          local={state.local}
          localActions={{
            install: (m) => client?.localInstall(m),
            cancel: (j) => client?.localCancel(j),
            remove: (m) => client?.localDelete(m),
            setDefault: (m) => setConfig('local_model', m),
            server: (a) => client?.localServer(a),
            refresh: () => client?.localRefresh(),
          }}
          initialTab={sheet}
          notices={state.appNotices}
          onDismissNotice={(id) => dispatch({ t: 'dismissAppNotice', id })}
          error={state.configError}
          onSet={setConfig}
          onClose={() => { setSheet(null); dispatch({ t: 'dismissConfigError' }) }}
          onRefreshCatalog={(force) => client?.send(Req.LIST_MODELS, { refresh: !!force })}
        />
      )}
      {session ? (
        <SessionView
          key={session.id}
          s={session}
          profile={state.profile}
          local={state.local}
          banner={
            <>
              {appNotices}
              {localCard && (
                <LocalSetupCard
                  local={localCard}
                  compact
                  onInstall={(m) => client?.localInstall(m)}
                  onCancel={(j) => client?.localCancel(j)}
                  onDecline={() => client?.localDecline()}
                  onOpenSettings={() => setSheet('local')}
                />
              )}
            </>
          }
          controls={
            <ControlBar
              status={session.status}
              settings={state.settings}
              catalog={state.catalog}
              local={state.local}
              onSet={setConfig}
              onOpenSettings={(tab) => tab === '__palette' ? setPalette(true) : setSheet(tab ?? 'model')}
              onEscalate={(harder) => client?.escalate(session.id, harder)}
              onDeescalate={() => client?.deescalate(session.id)}
            />
          }
          onSend={(text, attachments, refs) => {
            dispatch({ t: 'localUser', id: session.id, text,
                       attachments: attachments.map((a) => a.name), refs })
            client?.prompt(session.id, text, attachments, refs)
          }}
          onNeedFiles={() => client?.send(Req.FIND_FILES, { session: session.id })}
          onOpenRef={(p) => client?.send(Req.READ_FILE, { session: session.id, path: p })}
          fileUrl={session.file && client
            ? client.fileUrl(session.id, session.file.root ?? session.workspace, session.file.path)
            : null}
          onInterrupt={() => { dispatch({ t: 'stopping', id: session.id }); client?.interrupt(session.id) }}
          onEscalate={session.status?.policy?.policy === 'local-first' ? () => client?.escalate(session.id) : undefined}
          escalated={session.status?.policy?.tier === 'escalated'}
          onMode={(m) => client?.setMode(session.id, m)}
          onDecide={(d) => session.approval && client?.approve(session.id, session.approval.token, d)}
          onAnswer={(a) => {
            if (!session.ask) return
            client?.answer(session.id, session.ask.token, a)
            dispatch({ t: 'clearAsk', id: session.id, answers: a })
          }}
          onDismissAsk={() => {
            if (!session.ask) return
            client?.answer(session.id, session.ask.token, null)
            dispatch({ t: 'clearAsk', id: session.id })
          }}
          onStopServer={(pid) => client?.stopServer(session.id, pid)}
          onManageKey={openKeyDialog}
          onManageLocal={() => setSheet('local')}
          railOpen={railOpen}
          onRailResize={startRailResize}
          onCommands={() => setPalette(true)}
          onLoadJournal={() => session && client?.send(Req.GET_JOURNAL, { session: session.id })}
          railResizing={railDrag}
          onTermRun={(cmd) => client?.send(Req.TERM_RUN, { session: session.id, command: cmd })}
          onTermInterrupt={() => client?.send(Req.TERM_INTERRUPT, { session: session.id })}
          onTermClear={() => dispatch({ t: 'clearTerm', id: session.id })}
          onHideRail={() => setRailOpen(false)}
          onShowRail={() => setRailOpen(true)}
          onCloseFile={() => dispatch({ t: 'closeFile', id: session.id })}
          onMention={mentionPath}
          approvalIsTop={!palette && !sheet && !keyDialog && !tour && !confirmDelete && !confirmSwitch && !confirmClose}
        />
      ) : (
        <main className="empty">
          <div>
            <Wordmark className="empty-word" />
            <h2>No project open</h2>
            <p>Open a folder to start. The agent reads, writes and runs commands inside it — nowhere else, unless you say so.</p>
            <button className="btn primary" onClick={openFolder}>Open project…</button>
            {appNotices}
            {state.local && (
              <LocalSetupCard
                local={state.local}
                onInstall={(m) => client?.localInstall(m)}
                onCancel={(j) => client?.localCancel(j)}
                onDecline={() => client?.localDecline()}
                onOpenSettings={() => setSheet('local')}
              />
            )}
          </div>
        </main>
      )}
    </div>
  )
}

function SessionView({ s, controls, banner, profile, local, railOpen, onRailResize, railResizing, onSend, onInterrupt, onEscalate, escalated, onMode, onCommands, onLoadJournal, onDecide, approvalIsTop, onAnswer, onDismissAsk, onStopServer, onManageKey, onManageLocal, onHideRail, onShowRail, onTermRun, onTermInterrupt, onTermClear, onCloseFile, onMention, onNeedFiles, onOpenRef, fileUrl }: {
  s: SessionState
  controls: React.ReactNode
  /** App-level notices and the local download banner, above the transcript. */
  banner: React.ReactNode
  local: LocalStatus | null
  profile: Profile | null
  onSend: (t: string, a: { name: string; data_url: string }[], refs: string[]) => void
  onNeedFiles: () => void
  /** Click a badge in a sent message: open that file in the rail. */
  onOpenRef: (path: string) => void
  fileUrl: string | null
  onInterrupt: () => void
  onEscalate?: () => void
  escalated?: boolean
  onMode: (m: Mode) => void
  onCommands: () => void
  onLoadJournal: () => void
  onDecide: (d: 'allow' | 'deny' | 'always') => void
  onAnswer: (a: string[]) => void
  onDismissAsk: () => void
  onStopServer: (pid: number) => void
  onManageKey: () => void
  onManageLocal: () => void
  railOpen: boolean
  onRailResize: (e: React.PointerEvent) => void
  railResizing: boolean
  approvalIsTop: boolean
  onHideRail: () => void
  onShowRail: () => void
  onTermRun: (cmd: string) => void
  onTermInterrupt: () => void
  onTermClear: () => void
  onCloseFile: () => void
  onMention: (path: string) => void
}) {
  const scroller = useRef<HTMLDivElement>(null)

  // A file preview lives in the rail now — if the rail is hidden when one
  // arrives, opening a file would otherwise do nothing visible.
  const filePath = s.file?.path ?? null
  useEffect(() => {
    if (filePath && !railOpen) onShowRail()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filePath])
  const pinned = useRef(true)

  // Follow the stream, but stop fighting the user the moment they scroll up.
  useEffect(() => {
    const el = scroller.current
    if (el && pinned.current) el.scrollTop = el.scrollHeight
  }, [s.messages])

  const servers = s.status?.servers ?? []

  return (
    <>
      <main className="chat">
        <header className="chat-head">
          <div className="crumb">
            <strong>{s.title}</strong>
            <span title={s.workspace}>{shortPath(s.workspace, 4)}</span>
          </div>
          <div className="head-meta">
            {s.busy && <span className="pill live">working</span>}
          </div>
        </header>

        {banner}
        <div
          className="transcript"
          ref={scroller}
          onScroll={(e) => {
            const el = e.currentTarget
            pinned.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80
          }}
        >
          {s.messages.length === 0 && (
            <div className="starter">
              <h2>Ready when you are.</h2>
              <p>Try “add a health endpoint and run the tests”, or “explain how routing works in this repo”.</p>
            </div>
          )}

          {s.messages.map((m) => {
            switch (m.kind) {
              case 'user':
                return (
                  <div className="msg user" key={m.id}>
                    <div className="bubble">
                      {!!m.refs?.length && (
                        <div className="ref-row">
                          {m.refs.map((p) => <FileBadge key={p} path={p} onClick={() => onOpenRef(p)} />)}
                        </div>
                      )}
                      {m.text}
                      {!!m.attachments?.length && (
                        <div className="att-row">{m.attachments.map((a, i) => <span key={i}>{a}</span>)}</div>
                      )}
                    </div>
                  </div>
                )
              case 'assistant':
                return (
                  <div className="msg assistant" key={m.id}>
                    <Markdown text={m.text} />
                    {m.streaming && <span className="caret" />}
                  </div>
                )
              case 'tool':
                return <ToolCardView key={m.id} card={m.card} />
              case 'plan':
                return <PlanCard key={m.id} plan={m.plan} />
              case 'ask':
                return (
                  <AskInline
                    key={m.id}
                    ask={m.ask}
                    answered={m.answered}
                    onAnswer={onAnswer}
                    onDismiss={onDismissAsk}
                  />
                )
              case 'server':
                return (
                  <div className={`server-card ${m.stopped ? 'stopped' : ''}`} key={m.id}>
                    <div className="server-head">
                      <span className="live-dot" /> {m.stopped ? 'server stopped' : 'server running'}
                      {m.stopped
                        ? <span className="server-url dead">{m.url}</span>
                        : <a href={m.url} target="_blank" rel="noreferrer noopener">{m.url}</a>}
                    </div>
                    <code>{m.cmd}</code>
                  </div>
                )
              case 'notice':
                return (
                  <div className={`notice ${m.level}`} key={m.id}>
                    {m.text}
                    {m.detail && <pre>{typeof m.detail === 'string' ? m.detail : JSON.stringify(m.detail, null, 2)}</pre>}
                  </div>
                )
              case 'turn':
                return (
                  <div className="turn-foot" key={m.id} id={`m-${m.id}`}>
                    {m.model} · {m.hops} hop{m.hops === 1 ? '' : 's'} · {m.tokens.toLocaleString()} tok ·
                    {' '}{m.local && m.cost === 0 ? 'free · local' : `${m.estimated ? '~' : ''}$${m.cost.toFixed(6)}${m.local ? ' · partly local' : ''}`}{m.escalated ? ` · escalated to ${m.escalated.split('/').pop()}` : ''} · {m.elapsed.toFixed(1)}s
                  </div>
                )
            }
          })}

          {s.busy && <Thinking progress={s.progress} plan={s.plan} hop={s.hop} />}
        </div>

        <Composer
          busy={s.busy}
          stopping={s.stopping}
          mode={(s.status?.mode ?? 'default') as Mode}
          controls={controls}
          onSend={onSend}
          onInterrupt={onInterrupt}
          onEscalate={onEscalate}
          escalated={escalated}
          onMode={onMode}
          onCommands={onCommands}
          fileIndex={s.fileIndex}
          onNeedFiles={onNeedFiles}
        />
      </main>

      {railOpen ? (
      <Rail
        plan={s.plan}
        servers={servers}
        profile={profile}
        local={local}
        sessionCost={s.status?.session_cost ?? 0}
        sessionTokens={s.status?.session_tokens ?? 0}
        onStopServer={onStopServer}
        onManageKey={onManageKey}
        onManageLocal={onManageLocal}
        onHide={onHideRail}
        onResize={onRailResize}
        resizing={railResizing}
        journal={s.journal}
        onLoadJournal={onLoadJournal}
        file={s.file}
        fileUrl={fileUrl}
        onCloseFile={onCloseFile}
        onMention={onMention}
        terminal={
          <TerminalPanel
            lines={s.term.lines}
            cwd={s.term.cwd || s.workspace}
            shell={s.term.shell || 'shell'}
            onRun={onTermRun}
            onInterrupt={onTermInterrupt}
            onClear={onTermClear}
          />
        }
      />
      ) : (
        <button className="rail-show" onClick={onShowRail}
                aria-label="Show panel" title="Show panel — plan, preview, account">
          <Chevron dir="left" />
        </button>
      )}

      {s.approval && <ApprovalDialog approval={s.approval} onDecide={onDecide} isTop={approvalIsTop} />}

    </>
  )
}


/** Palette entries that aren't settings — things you DO, not things you set. */
function paletteActions(
  openFolder: () => void,
  browseModels: () => void,
  compact: () => void,
  manageKey: () => void,
  startTour: () => void,
  setTheme: (t: 'system' | 'light' | 'dark') => void,
  setMode: (m: string) => void,
  manageLocal: () => void,
  restartLocal: (() => void) | null,
): Action[] {
  return [
    // One group: only Appearance and Permissions have enough members to
    // earn a section of their own.
    { id: 'open', title: 'Open project…', group: 'Actions', keywords: 'folder workspace directory', run: openFolder },
    { id: 'models', title: 'Browse models', group: 'Actions',
      hint: 'Compare every model on price and context', keywords: 'catalog price context switch', run: browseModels },
    { id: 'compact', title: 'Compact history now', group: 'Actions',
      hint: 'Summarise older turns to free up context', keywords: 'context shrink summarise', run: compact },
    { id: 'key', title: 'Replace API key', group: 'Actions',
      hint: 'Connect a different Mesh key, or sign out',
      keywords: 'login signin sign in credential token auth apikey', run: manageKey },
    { id: 'tour', title: 'Take the guided tour', group: 'Actions',
      hint: 'A two-minute walkthrough anchored to the real UI',
      keywords: 'walkthrough onboarding help intro getting started features', run: startTour },
    { id: 'local', title: 'Manage local models', group: 'Actions',
      hint: 'Download, delete or switch the model that runs on this machine',
      keywords: 'local offline llama gguf download free machine', run: manageLocal },
    ...(restartLocal ? [{ id: 'local-restart', title: 'Restart the local model server', group: 'Actions',
      hint: 'Reload the weights, for example after changing context or GPU settings',
      keywords: 'local llama server restart reload', run: restartLocal }] : []),
    ...([
      ['system', 'Match system', 'Follow your macOS appearance setting'],
      ['light', 'Light', ''],
      ['dark', 'Dark', ''],
    ] as const).map(([id, label, hint]) => ({
      id: `theme:${id}`,
      title: `Appearance: ${label}`,
      hint,
      group: 'Appearance',
      keywords: 'theme dark light mode colour color appearance',
      run: () => setTheme(id),
    })),
    ...MODES.map((m) => ({
      id: `mode:${m.id}`,
      title: `Permissions: ${m.label}`,
      hint: m.blurb,
      group: 'Permissions',
      keywords: 'approve approval safety mode gate',
      run: () => setMode(m.id),
    })),
  ]
}
