import { useEffect, useState } from 'react'
import type { Plan, Profile } from '../types'
import { Chevron } from './Chevron'
import { PlanPanel } from './PlanPanel'
import { ServerPanel } from './ServerPanel'
import { Preview } from './Preview'
import { ProfilePanel } from './ProfilePanel'
import { Markdown } from './Markdown'
import { FileViewer } from './FileViewer'
import type { FilePreview } from '../types'

type Tab = 'plan' | 'preview' | 'terminal' | 'journal' | 'profile' | 'file'

const TAB_LABEL: Record<Tab, string> = {
  file: 'File', plan: 'Plan', preview: 'Preview', terminal: 'Shell',
  journal: 'Journal', profile: 'Account',
}

const TAB_PATH: Record<Tab, string> = {
  // Same grammar as the sidebar's nav icons: 16px grid, 1.4 stroke, plain
  // geometric outlines, no fills, no double strokes.
  file: 'M4 1.5h5l3 3v10H4z M9 1.5v3h3',
  plan: 'M2.5 4.5l1.2 1.2L6 3.3 M8 4.5h5.5 M2.5 8.5l1.2 1.2L6 7.3 M8 8.5h5.5 M2.5 12.5l1.2 1.2L6 11.3 M8 12.5h5.5',
  preview: 'M2 4.5A1.5 1.5 0 0 1 3.5 3h9A1.5 1.5 0 0 1 14 4.5v7a1.5 1.5 0 0 1-1.5 1.5h-9A1.5 1.5 0 0 1 2 11.5z M2 6.5h12',
  terminal: 'M2 4.5A1.5 1.5 0 0 1 3.5 3h9A1.5 1.5 0 0 1 14 4.5v7a1.5 1.5 0 0 1-1.5 1.5h-9A1.5 1.5 0 0 1 2 11.5z M5 6.5 7 8.5 5 10.5 M8.5 10.5h3',
  journal: 'M4.5 2h7A1.5 1.5 0 0 1 13 3.5v9a1.5 1.5 0 0 1-1.5 1.5h-7A1.5 1.5 0 0 1 3 12.5v-9A1.5 1.5 0 0 1 4.5 2z M2 5h2 M2 8h2 M2 11h2 M7 6h3.5 M7 9h3.5',
  profile: 'M8 8a2.6 2.6 0 1 0 0-5.2A2.6 2.6 0 0 0 8 8z M2.8 13.5a5.2 5.2 0 0 1 10.4 0',
}

function TabIcon({ tab }: { tab: Tab }) {
  return (
    <svg viewBox="0 0 16 16" className="tab-icon" aria-hidden fill="none"
         stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round">
      <path d={TAB_PATH[tab]} />
    </svg>
  )
}

/**
 * The right rail, as tabs rather than a stack.
 *
 * Plan, a live preview of whatever the agent just started, and account
 * state are all "context beside the conversation" — stacking them meant
 * whichever came last was permanently below the fold. Tabs give each one
 * the full column.
 *
 * The tab auto-switches to Preview the first time a server appears, because
 * that is the one moment where the thing you want to look at is definitely
 * not the plan. It never steals focus again after that.
 */
export function Rail({ plan, servers, profile, sessionCost, sessionTokens,
                      onStopServer, onManageKey, onHide, terminal,
                      journal, onLoadJournal, onResize, resizing,
                      file, fileUrl, onCloseFile, onMention }: {
  plan: Plan | null
  servers: { pid: number; port: number; url: string; cmd: string }[]
  profile: Profile | null
  sessionCost: number
  sessionTokens: number
  onStopServer: (pid: number) => void
  onManageKey: () => void
  onHide: () => void
  onResize: (e: React.PointerEvent) => void
  resizing: boolean
  terminal: React.ReactNode
  journal: { text: string; path: string } | null
  onLoadJournal: () => void
  /** The file being previewed, docked here as its own tab. */
  file: FilePreview | null
  /** Engine URL for the file's bytes, or null when the root is unknown. */
  fileUrl: string | null
  onCloseFile: () => void
  onMention: (path: string) => void
}) {
  // Seed autoShown from whether a server is ALREADY running. Rail is
  // remounted per session (keyed on session.id), so a fresh `false` made it
  // re-yank to Preview every time you returned to a session that had a live
  // server — breaking its own "never steals focus again" promise. Seeding
  // true when servers already exist means the auto-switch fires once, at the
  // moment a server first appears, and never on a plain revisit.
  const [tab, setTab] = useState<Tab>(() => (servers.length ? 'preview' : 'plan'))
  const [autoShown, setAutoShown] = useState(() => servers.length > 0)

  useEffect(() => {
    if (servers.length && !autoShown) {
      setTab('preview')
      setAutoShown(true)
    }
  }, [servers.length, autoShown])

  useEffect(() => {
    if (tab === 'journal') onLoadJournal()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab])

  // Opening a file (tree click, or a path clicked in the chat) is an
  // explicit "show me this" — jump to it. Closing it drops back to Plan
  // rather than leaving a tab selected that no longer exists.
  const filePath = file?.path ?? null
  useEffect(() => {
    if (filePath) setTab('file')
    else setTab((t) => (t === 'file' ? 'plan' : t))
  }, [filePath])
  // Popped out over the app; docks back on Escape, the dock button, or
  // when the file is closed. A newly opened file replaces it in place.
  const [popped, setPopped] = useState(false)
  useEffect(() => { if (!filePath) setPopped(false) }, [filePath])

  const tabs: Tab[] = file
    ? ['file', 'plan', 'preview', 'terminal', 'journal', 'profile']
    : ['plan', 'preview', 'terminal', 'journal', 'profile']

  return (
    <aside className="rail">
      {/* Grip on the LEFT edge — the rail is anchored right. */}
      <div
        className={`rail-resize ${resizing ? 'dragging' : ''}`}
        onPointerDown={onResize}
        title="Drag to resize"
      />
      <div className="rail-head">
        {/* Icon tabs. Six worded tabs never fit a 300px rail — the last
            one was clipped under the collapse chevron. The active tab
            shows its word; the rest show their icon and say their name on
            hover. The file tab always carries its name. */}
        <div className="rail-tabs" role="tablist">
          {tabs.map((t) => {
            const on = tab === t
            const label = t === 'file' ? (file?.path.split('/').pop() ?? 'File')
              : TAB_LABEL[t]
            return (
              <button
                key={t}
                role="tab"
                aria-selected={on}
                aria-label={label}
                title={t === 'file' ? file?.path : label}
                className={`${on ? 'on' : ''} ${t === 'file' ? 'is-file' : ''}`}
                onClick={() => setTab(t)}
              >
                <TabIcon tab={t} />
                {(on || t === 'file') && <span className="tab-word">{label}</span>}
                {t === 'preview' && servers.length > 0 && <span className="tab-dot" />}
              </button>
            )
          })}
        </div>
        <button className="rail-toggle" onClick={onHide} aria-label="Hide panel" title="Hide panel">
          <Chevron dir="right" />
        </button>
      </div>

      <div className={`rail-body ${tab === 'file' ? 'flush' : ''}`}>
        {tab === 'file' && file && (
          popped
            ? <>
                <FileViewer file={file} url={fileUrl} onClose={onCloseFile} onMention={onMention}
                            popped onPop={() => setPopped(true)} onDock={() => setPopped(false)} />
                <button className="rail-empty viewer-docklink" onClick={() => setPopped(false)}>
                  <strong>{file.path.split('/').pop()} is open as a popup.</strong>
                  <p>Click here or press Escape to dock it back.</p>
                </button>
              </>
            : <FileViewer file={file} url={fileUrl} onClose={onCloseFile} onMention={onMention}
                          popped={false} onPop={() => setPopped(true)} onDock={() => setPopped(false)} />
        )}

        {tab === 'plan' && (
          plan
            ? <PlanPanel plan={plan} />
            : <Empty
                title="No plan yet"
                body="For anything multi-step the agent writes a checklist first, and it appears here as it works through it."
              />
        )}

        {tab === 'preview' && (
          servers.length
            ? (
              <>
                <Preview servers={servers} />
                <ServerPanel servers={servers} onStop={onStopServer} />
              </>
            )
            : <Empty
                title="Nothing running"
                body="When the agent starts a dev server, it appears here live — no switching to a browser."
              />
        )}

        {tab === 'terminal' && terminal}

        {tab === 'journal' && (
          <div className="journal-panel">
            {journal?.text?.trim() ? (
              <Markdown text={journal.text} />
            ) : (
              <div className="rail-empty">
                <strong>Nothing in the journal yet.</strong>
                <p>Mesh Code keeps a product-manager style notebook per
                project — how it runs, what's built, and what was learned —
                written automatically as the agent works. It feeds every new
                session so nothing gets rebuilt from scratch.</p>
              </div>
            )}
          </div>
        )}

        {tab === 'profile' && (
          <ProfilePanel
            profile={profile}
            sessionCost={sessionCost}
            sessionTokens={sessionTokens}
            onManageKey={onManageKey}
          />
        )}
      </div>
    </aside>
  )
}

function Empty({ title, body }: { title: string; body: string }) {
  return (
    <div className="rail-empty">
      <strong>{title}</strong>
      <p>{body}</p>
    </div>
  )
}
