import { useRef, useState } from 'react'
import { useDismissable } from '../useDismissable'
import type { Profile, SessionSummary } from '../types'
import { Mark } from './Logo'
import { Caret, Chevron, IconExternal } from './Chevron'
import { FeedbackDialog } from './FeedbackDialog'

/**
 * Navigation, history, and account — the three things a sidebar is for.
 *
 * Sessions are grouped by day rather than listed flat: once you have more
 * than a handful, "which of these was I working on yesterday" is the actual
 * question, and a flat list cannot answer it.
 *
 * The account row sits at the bottom and opens upward, holding everything
 * that is about YOU rather than about the current project — settings, the
 * key, appearance, help. Keeping those out of the main nav is what stops
 * the top of the sidebar becoming a junk drawer.
 */
const ICONS = {
  new: 'M8 3v10M3 8h10',
  folder: 'M2 4.5A1.5 1.5 0 0 1 3.5 3h2.8l1.2 1.5h5A1.5 1.5 0 0 1 14 6v5.5A1.5 1.5 0 0 1 12.5 13h-9A1.5 1.5 0 0 1 2 11.5z',
  models: 'M8 2.2 13.5 5.4v5.2L8 13.8 2.5 10.6V5.4zM8 8.2l5.5-2.8M8 8.2v5.6M8 8.2 2.5 5.4',
  settings: 'M8 10.2a2.2 2.2 0 1 0 0-4.4 2.2 2.2 0 0 0 0 4.4z M13 8a5 5 0 0 0-.1-1l1.2-.9-1.2-2-1.4.5a5 5 0 0 0-1.7-1L9.6 2H6.4l-.2 1.6a5 5 0 0 0-1.7 1l-1.4-.5-1.2 2 1.2.9a5 5 0 0 0 0 2l-1.2.9 1.2 2 1.4-.5a5 5 0 0 0 1.7 1L6.4 14h3.2l.2-1.6a5 5 0 0 0 1.7-1l1.4.5 1.2-2-1.2-.9A5 5 0 0 0 13 8z',
}

function Icon({ d }: { d: string }) {
  return (
    <svg viewBox="0 0 16 16" className="nav-icon" aria-hidden
         fill="none" stroke="currentColor" strokeWidth="1.4"
         strokeLinecap="round" strokeLinejoin="round">
      <path d={d} />
    </svg>
  )
}

export function Sidebar({
  sessions, profile, theme, connection, busyAnywhere,
  onNewSession, onOpenFolder, onBrowseModels, onSettings,
  onPalette, onTour, tourTaken, onManageKey, onSetTheme,
  onSubmitFeedback, onResize, resizing, explorer, onHide,
}: {
  sessions: SessionSummary[]
  profile: Profile | null
  theme: 'system' | 'light' | 'dark'
  connection: 'connecting' | 'open' | 'closed'
  busyAnywhere: boolean
  onNewSession: () => void
  onOpenFolder: () => void
  onBrowseModels: () => void
  onSettings: () => void
  onPalette: () => void
  onTour: () => void
  tourTaken: boolean
  onManageKey: () => void
  onSetTheme: (t: 'system' | 'light' | 'dark') => void
  onSubmitFeedback: (rating: number, good: string, bad: string) => Promise<boolean>
  explorer: React.ReactNode
  onResize: (e: React.PointerEvent) => void
  resizing: boolean
  onHide: () => void
}) {
  const [menu, setMenu] = useState(false)
  const [feedback, setFeedback] = useState(false)
  const menuRef = useRef<HTMLDivElement>(null)

  useDismissable(menu, () => setMenu(false), menuRef)


  return (
    <aside className="sidebar">
      <div className="side-head">
        <Mark className="side-mark" animate={busyAnywhere} />
        <span className="side-product">Mesh <b>Code</b></span>
        <button className="side-toggle" onClick={onHide}
                aria-label="Hide sidebar" title="Hide sidebar (⌘B)">
          <Chevron dir="left" />
        </button>
      </div>

      {/* One row of icons, not four stacked rows: the tree below is what
          you work in, and 170px of navigation above it was dead weight.
          Labels live in the tooltips and the palette (⌘K). */}
      <nav className="side-nav row" aria-label="Main">
        <button onClick={onNewSession} title="New session" aria-label="New session">
          <Icon d={ICONS.new} /><span>New</span>
        </button>
        <button onClick={onOpenFolder} title="Open project…" aria-label="Open project">
          <Icon d={ICONS.folder} /><span>Open</span>
        </button>
        <button onClick={onBrowseModels} title="Models" aria-label="Models">
          <Icon d={ICONS.models} /><span>Models</span>
        </button>
        <button onClick={onSettings} title="Settings (⌘,)" aria-label="Settings">
          <Icon d={ICONS.settings} /><span>Settings</span>
        </button>
      </nav>

      {/* The engine link. Silent while open; visible (with its pre-styled
          .conn row, which nothing rendered before) the moment it drops, so a
          dead engine no longer looks like a hung app. */}
      {connection !== 'open' && (
        <div className={`conn ${connection}`}>
          {connection === 'connecting' ? 'Connecting to the engine…'
            : 'Engine disconnected — reconnecting…'}
        </div>
      )}

      {/* Sessions and files were two parallel lists showing the same
          project names — merged: the PROJECTS tree below is both. Each
          project node is its session (click = its chat + its files). */}
      <div className="side-scroll">
        {sessions.length === 0 && (
          <p className="side-hint">Open a project folder to start.</p>
        )}
        {explorer}
      </div>

      {/* Two quiet links, not two cards: the tour and the feedback form
          are worth one line each, not a third of the sidebar. The tour
          link keeps a brand dot until it has been taken once. */}
      <div className="side-links">
        <button className={`side-link ${tourTaken ? '' : 'fresh'}`} onClick={onTour}>
          <span className="side-link-dot" aria-hidden />Take the tour
        </button>
        <span className="side-link-sep" aria-hidden>·</span>
        <button className="side-link" onClick={() => setFeedback(true)} title="Beta — rate it and tell us what broke">
          Send feedback
        </button>
      </div>
      {feedback && (
        <FeedbackDialog onSubmit={onSubmitFeedback} onClose={() => setFeedback(false)} />
      )}

      <div className="side-foot" ref={menuRef}>
        {menu && (
          <div className="side-menu">
            {/* Identity first, and honest about it: the gateway exposes no
                name, email or plan to a data-plane key, so the key itself
                and where it came from IS the identity we can show. */}
            <div className="menu-identity">
              <span className="avatar lg">{initial(profile)}</span>
              <span>
                <strong>{profile?.signed_in ? 'Signed in to Mesh' : 'Not signed in'}</strong>
                <em>{profile?.key_hint ? `Key ${profile.key_hint}` : 'No key stored'}
                  {profile?.key_source && ` · ${profile.key_source}`}</em>
              </span>
            </div>

            <div className="menu-section">
              <button onClick={() => { setMenu(false); onSettings() }}>
                Settings <kbd>⌘,</kbd>
              </button>
              <button onClick={() => { setMenu(false); onPalette() }}>
                Commands <kbd>⌘K</kbd>
              </button>
              <button onClick={() => { setMenu(false); onManageKey() }}>
                {profile?.signed_in ? 'Replace API key' : 'Connect an API key'}
              </button>
            </div>

            <div className="menu-block">
              <span className="menu-label">Appearance</span>
              <div className="seg">
                {(['system', 'light', 'dark'] as const).map((t) => (
                  <button key={t} className={theme === t ? 'on' : ''}
                          onClick={() => onSetTheme(t)}>
                    {t === 'system' ? 'Auto' : t === 'light' ? 'Light' : 'Dark'}
                  </button>
                ))}
              </div>
            </div>

            <div className="menu-section">
              <button onClick={() => { setMenu(false); onTour() }}>Guided tour</button>
              <button onClick={() => { setMenu(false)
                window.open('https://docs.meshapi.ai', '_blank', 'noopener') }}>
                Documentation
              </button>
              <button onClick={() => { setMenu(false)
                window.open(profile?.account_url ?? 'https://app.meshapi.ai', '_blank', 'noopener') }}>
                Account &amp; billing <span className="ext"><IconExternal size={11} /></span>
              </button>
            </div>
          </div>
        )}

        <button className="side-account" onClick={() => setMenu((m) => !m)}>
          <span className="avatar">{initial(profile)}</span>
          <span className="account-text">
            <strong>{profile?.signed_in ? 'Mesh account' : 'No key'}</strong>
            <em>
              {profile?.models ? `${profile.models.toLocaleString()} models` : 'Not connected'}
              {profile?.lifetime?.cost ? ` · $${profile.lifetime.cost.toFixed(2)}` : ''}
            </em>
          </span>
          <span className={`account-status ${connection}`} title={`Engine ${connection}`} />
          <span className="account-caret"><Caret open /></span>
        </button>
      </div>

      <div
        className={`side-resize ${resizing ? 'dragging' : ''}`}
        onPointerDown={onResize}
        title="Drag to resize · double-click to reset"
      />
    </aside>
  )
}

/** Opens the in-app feedback dialog — stars and two boxes, submitted in
 *  the background through the engine's /api/feedback relay to the "Mesh
 *  Code Beta Feedback" Google Form. Nobody leaves the app to answer. */
/** The key's last character is meaningless as an identity; the gateway
 *  gives us no name to use, so the mark stands in for the account. */
function initial(profile: Profile | null) {
  return profile?.signed_in ? 'M' : '—'
}

/** Today / Yesterday / an actual date — the question people actually ask. */
