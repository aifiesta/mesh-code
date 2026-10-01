import { useCallback, useEffect, useMemo, useState } from 'react'
import type { DirEntry, Root } from '../types'
import { Caret } from './Chevron'

/**
 * ONE files panel for every open folder.
 *
 * This used to render one full panel PER root — each with its own header,
 * badge, filter box and empty state — so two folders meant two of
 * everything stacked, and the project's name appeared once in the session
 * list and again as a section header. Now there is a single "Files"
 * section: one filter, and each root is a collapsible top-level node in
 * the same tree (the VS Code multi-root shape). The active project carries
 * a dot; browse-only roots are just nodes you can expand or promote.
 *
 * Lazy by directory: expanding a node asks the engine for that one level,
 * so a 40k-file `node_modules` costs nothing until you go looking inside.
 * Clicking a file previews it; Cmd/Ctrl-click drops its path into the
 * composer.
 */
export function Explorer({ roots, activeRoot, listing, activePath,
                          onList, onOpen, onMention, onCreate, onRename,
                          onDelete, onClose, onMakeActive,
                          onActivateSession, onCloseSession, onNewChat,
                          chats, activeChat, onSwitchChat }: {
  roots: Root[]
  activeRoot: string
  /** `${root}|${path}` -> entries, filled in as directories are expanded. */
  listing: Record<string, DirEntry[]>
  activePath: string | null
  onList: (root: string, path: string) => void
  onOpen: (root: string, path: string) => void
  onMention: (path: string) => void
  onCreate: (root: string, path: string, kind: 'file' | 'dir') => void
  onRename: (root: string, path: string, name: string) => void
  onDelete: (root: string, entry: DirEntry) => void
  onClose: (root: string) => void
  onMakeActive: (root: string, name: string) => void
  /** Switch to the session behind a project node. */
  onActivateSession: (sessionId: string) => void
  /** End a session (its chat and servers) and drop the node. */
  onCloseSession: (sessionId: string) => void
  /** Another chat on this project — existing chats stay switchable. */
  onNewChat: (rootPath: string) => void
  /** workspace path -> that project's chats, for the Chats view. */
  chats: Record<string, { id: string; label: string; createdAt?: number; busy: boolean }[]>
  activeChat: string
  onSwitchChat: (id: string) => void
}) {
  const [query, setQuery] = useState('')
  /** Files or Chats — one panel, two lenses on the same project nodes. */
  const [view, setView] = useState<'files' | 'chats'>(() => {
    try { return (localStorage.getItem('mesh.projview') as any) || 'files' } catch { return 'files' }
  })
  const setViewKeep = (v: 'files' | 'chats') => {
    setView(v)
    try { localStorage.setItem('mesh.projview', v) } catch { /* ignore */ }
  }
  /** Expanded state, namespaced `${root}|${path}` so equal relative paths
   *  in different roots can't shadow each other. */
  const [open, setOpen] = useState<Record<string, boolean>>({})
  const [draft, setDraft] = useState<
    | { root: string; dir: string; kind: 'file' | 'dir' }
    | { root: string; rename: string; current: string }
    | null>(null)
  const [menu, setMenu] = useState<{
    x: number; y: number; root: string; entry: DirEntry | null } | null>(null)

  useEffect(() => {
    if (!menu) return
    const close = () => setMenu(null)
    window.addEventListener('pointerdown', close)
    window.addEventListener('keydown', close)
    return () => {
      window.removeEventListener('pointerdown', close)
      window.removeEventListener('keydown', close)
    }
  }, [menu])

  // A root node is open when toggled — and the ACTIVE project starts open,
  // browse roots start closed. That alone removes most of the old clutter:
  // a folder you're merely peeking at is one quiet line.
  const rootOpen = (path: string) => open[`root:${path}`] ?? (path === activeRoot)

  const toggleRoot = (r: Root) => {
    // An inactive PROJECT node is another session: clicking it switches to
    // that chat, and its files arrive with it. One gesture, both things.
    if (r.sessionId && r.path !== activeRoot) {
      onActivateSession(r.sessionId)
      return
    }
    const next = !rootOpen(r.path)
    setOpen((o) => ({ ...o, [`root:${r.path}`]: next }))
    if (next && listing[`${r.path}|`] === undefined) onList(r.path, '')
  }

  // Fetch the active root's top level when it's missing (first open, or a
  // session whose tree hasn't loaded this root yet). Presence-guarded so it
  // can't loop.
  const activeLoaded = listing[`${activeRoot}|`] !== undefined
  useEffect(() => {
    if (activeRoot && !activeLoaded) onList(activeRoot, '')
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeRoot, activeLoaded])

  const toggle = useCallback((root: string, path: string) => {
    const key = `${root}|${path}`
    setOpen((o) => {
      const next = { ...o, [key]: !o[key] }
      if (next[key] && !listing[`${root}|${path}`]) onList(root, path)
      return next
    })
  }, [listing, onList])

  const startCreate = (root: string, dir: string, kind: 'file' | 'dir') => {
    setOpen((o) => ({ ...o, [`root:${root}`]: true, [`${root}|${dir}`]: true }))
    if (!listing[`${root}|${dir}`]) onList(root, dir)
    setDraft({ root, dir, kind })
  }

  return (
    <div className="explorer">
      <div className="explorer-sticky">
        <div className="explorer-head">
          <span className="side-group-label files-label">Projects</span>
          <div className="seg view-seg" role="tablist">
            <button className={view === 'files' ? 'on' : ''} role="tab"
                    aria-selected={view === 'files'}
                    onClick={() => setViewKeep('files')}>Files</button>
            <button className={view === 'chats' ? 'on' : ''} role="tab"
                    aria-selected={view === 'chats'}
                    onClick={() => setViewKeep('chats')}>Chats</button>
          </div>

        </div>
        <input
          className="explorer-filter"
          placeholder={view === 'files' ? 'Filter files…' : 'Filter chats…'}
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
      </div>

      <div className="explorer-tree" role="tree">
        {roots.map((r) => {
          const expanded = rootOpen(r.path)
          const isActive = r.path === activeRoot
          return (
            <div key={r.path}>
              <button
                className={`tree-row root ${isActive ? 'active-root' : ''}`}
                title={r.path}
                onClick={() => toggleRoot(r)}
                onContextMenu={(ev) => {
                  ev.preventDefault()
                  setMenu({ x: ev.clientX, y: ev.clientY, root: r.path, entry: null })
                }}
              >
                <span className={`root-chevron ${expanded ? 'down' : ''}`} aria-hidden>
                  <svg viewBox="0 0 16 16" width="13" height="13"
                       fill="none" stroke="currentColor" strokeWidth="1.7"
                       strokeLinecap="round" strokeLinejoin="round">
                    <path d="M6 3.5 10.5 8 6 12.5" />
                  </svg>
                </span>
                <span className="tree-name">{r.name}</span>
                {r.busy && !isActive && <span className="busy-dot" title="Working" />}
                {!r.sessionId && !isActive && (
                  <>
                    <span className="root-hint"
                          role="button"
                          title="Browsing only — click to open as the project"
                          onClick={(ev) => { ev.stopPropagation(); onMakeActive(r.path, r.name) }}>
                      open
                    </span>
                    {/* A closed project lingers here as a browse folder so it
                        can be reopened in one click — but the only way to
                        drop it used to be a right-click menu nobody found. */}
                    <span className="root-x"
                          role="button"
                          aria-label="Remove from sidebar"
                          title="Remove from sidebar"
                          onClick={(ev) => { ev.stopPropagation(); onClose(r.path) }}>
                      <svg viewBox="0 0 16 16" width="11" height="11" fill="none"
                           stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
                        <path d="M4 4l8 8M12 4l-8 8" />
                      </svg>
                    </span>
                  </>
                )}
              </button>
              {expanded && view === 'chats' && (
                <div className="chat-list">
                  {(chats[r.path] ?? [])
                    .filter((c) => !query.trim()
                      || c.label.toLowerCase().includes(query.trim().toLowerCase()))
                    .map((c) => (
                    <button key={c.id}
                            className={`tree-row chat-item ${c.id === activeChat ? 'on' : ''}`}
                            style={{ paddingLeft: 20 }}
                            onClick={() => { if (c.id !== activeChat) onSwitchChat(c.id) }}>
                      <span className="chat-ico" aria-hidden>
                        <svg viewBox="0 0 16 16" width="12" height="12" fill="none"
                             stroke="currentColor" strokeWidth="1.4"
                             strokeLinecap="round" strokeLinejoin="round">
                          <path d="M2.5 3.5h11v7h-6l-3 2.5v-2.5h-2z" />
                        </svg>
                      </span>
                      <span className="tree-name">{c.label}</span>
                      <span className="chat-menu-date">
                        {c.busy ? '●' : fmtDay(c.createdAt)}
                      </span>
                    </button>
                  ))}
                  {r.sessionId && (
                    <button className="tree-row chat-item chat-new" style={{ paddingLeft: 20 }}
                            onClick={() => onNewChat(r.path)}>
                      <span className="chat-ico" aria-hidden>+</span>
                      <span className="tree-name">New chat</span>
                    </button>
                  )}
                </div>
              )}
              {expanded && view === 'files' && (
                <Level
                  root={r.path} path="" depth={1}
                  listing={listing} open={open} query={query}
                  toggle={toggle} onOpen={onOpen} onMention={onMention}
                  activePath={activePath} draft={draft} setDraft={setDraft}
                  onCreate={onCreate} onRename={onRename}
                  onContext={(e, entry) => {
                    e.preventDefault()
                    setMenu({ x: e.clientX, y: e.clientY, root: r.path, entry })
                  }}
                />
              )}
            </div>
          )
        })}
      </div>

      {menu && (
        <div className="tree-menu" style={{ left: menu.x, top: menu.y }}
             onPointerDown={(e) => e.stopPropagation()}>
          {(() => {
            const e = menu.entry
            const dir = e ? (e.dir ? e.path : parentOf(e.path)) : ''
            const isActive = menu.root === activeRoot
            const rootName = roots.find((r) => r.path === menu.root)?.name ?? menu.root
            return (
              <>
                <button onClick={() => { setMenu(null); startCreate(menu.root, dir, 'file') }}>New file</button>
                <button onClick={() => { setMenu(null); startCreate(menu.root, dir, 'dir') }}>New folder</button>
                {e ? <>
                  <hr />
                  <button onClick={() => { setMenu(null); setDraft({ root: menu.root, rename: e.path, current: e.name }) }}>
                    Rename
                  </button>
                  <button onClick={() => { setMenu(null); navigator.clipboard?.writeText(e.path) }}>
                    Copy path
                  </button>
                  {!e.dir && <button onClick={() => { setMenu(null); onMention(e.path) }}>
                    Mention in chat
                  </button>}
                  <hr />
                  <button className="danger" onClick={() => { setMenu(null); onDelete(menu.root, e) }}>
                    Delete
                  </button>
                </> : <>
                  <hr />
                  <button onClick={() => { setMenu(null); onList(menu.root, '') }}>Refresh</button>
                  <button onClick={() => { setMenu(null); setOpen({}) }}>Collapse all</button>
                  {(() => {
                    const node = roots.find((r) => r.path === menu.root)
                    if (node?.sessionId) {
                      return <>
                        <button onClick={() => { setMenu(null); onNewChat(menu.root) }}>
                          New chat
                        </button>
                        <button className="danger"
                                onClick={() => { setMenu(null); onCloseSession(node.sessionId!) }}>
                          Close project (ends its chat)
                        </button>
                      </>
                    }
                    return <>
                      {!isActive && (
                        <button onClick={() => { setMenu(null); onMakeActive(menu.root, rootName) }}>
                          Open as project
                        </button>
                      )}
                      {!isActive && (
                        <button className="danger" onClick={() => { setMenu(null); onClose(menu.root) }}>
                          Remove from sidebar
                        </button>
                      )}
                    </>
                  })()}
                </>}
              </>
            )
          })()}
        </div>
      )}
    </div>
  )
}


const parentOf = (p: string) => (p.includes('/') ? p.slice(0, p.lastIndexOf('/')) : '')

const fmtDay = (t?: number) =>
  t ? new Date(t * 1000).toLocaleDateString(undefined, { month: 'short', day: 'numeric' }) : ''

/** The in-place name input used for both new entries and renames. */
function NameInput({ initial, depth, kind, onCommit, onCancel }: {
  initial: string; depth: number; kind: 'file' | 'dir'
  onCommit: (name: string) => void; onCancel: () => void
}) {
  const [value, setValue] = useState(initial)
  return (
    <div className="tree-row draft" style={{ paddingLeft: depth * 12 + 8 }}>
      <span className="tree-caret" />
      <span className={`tree-icon ${kind === 'dir' ? 'folder' : 'plain'}`} aria-hidden />
      <input
        autoFocus
        className="tree-input"
        value={value}
        onChange={(e) => setValue(e.target.value)}
        onBlur={() => (value.trim() ? onCommit(value.trim()) : onCancel())}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && value.trim()) onCommit(value.trim())
          if (e.key === 'Escape') onCancel()
        }}
      />
    </div>
  )
}

function Level({ root, path, depth, listing, open, query, toggle, onOpen, onMention,
                activePath, draft, setDraft, onCreate, onRename, onContext }: {
  root: string
  path: string; depth: number
  listing: Record<string, DirEntry[]>
  open: Record<string, boolean>
  query: string
  toggle: (root: string, p: string) => void
  onOpen: (root: string, p: string) => void
  onMention: (p: string) => void
  activePath: string | null
  draft: any
  setDraft: (d: any) => void
  onCreate: (root: string, path: string, kind: 'file' | 'dir') => void
  onRename: (root: string, path: string, name: string) => void
  onContext: (e: React.MouseEvent, entry: DirEntry | null) => void
}) {
  const entries = listing[`${root}|${path}`]
  const q = query.trim().toLowerCase()

  const shown = useMemo(() => {
    if (!entries) return []
    if (!q) return entries
    // While filtering, keep directories so their matching children stay
    // reachable — a filter that hides the path to a hit is useless.
    return entries.filter((e) => e.dir || e.name.toLowerCase().includes(q))
  }, [entries, q])

  if (!entries) return <div className="tree-loading" style={{ paddingLeft: depth * 12 + 10 }}>…</div>

  return (
    <>
      {draft && 'dir' in draft && draft.root === root && draft.dir === path && (
        <NameInput
          initial="" depth={depth} kind={draft.kind}
          onCommit={(name) => {
            onCreate(root, path ? `${path}/${name}` : name, draft.kind)
            setDraft(null)
          }}
          onCancel={() => setDraft(null)}
        />
      )}
      {shown.map((e) => {
        const isOpen = !!open[`${root}|${e.path}`]
        if (draft && 'rename' in draft && draft.root === root && draft.rename === e.path) {
          return (
            <NameInput
              key={e.path} initial={draft.current} depth={depth}
              kind={e.dir ? 'dir' : 'file'}
              onCommit={(name) => { onRename(root, e.path, name); setDraft(null) }}
              onCancel={() => setDraft(null)}
            />
          )
        }
        return (
          <div key={e.path}>
            <button
              onContextMenu={(ev) => onContext(ev, e)}
              draggable={!e.dir}
              onDragStart={(ev) => {
                // Dragging a file into the composer references it — the
                // path rides on a private type so an image drop stays an
                // image drop.
                ev.dataTransfer.setData('application/x-mesh-path', e.path)
                ev.dataTransfer.setData('text/plain', e.path)
                ev.dataTransfer.effectAllowed = 'link'
              }}
              className={`tree-row ${e.dir ? 'dir' : 'file'} ${e.noisy ? 'noisy' : ''} ${e.hidden ? 'hidden' : ''} ${activePath === e.path ? 'on' : ''}`}
              style={{ paddingLeft: depth * 12 + 8 }}
              title={e.path}
              onClick={(ev) => {
                if (e.dir) return toggle(root, e.path)
                if (ev.metaKey || ev.ctrlKey) return onMention(e.path)
                onOpen(root, e.path)
              }}
            >
              <span className="tree-caret">{e.dir ? <Caret open={isOpen} /> : null}</span>
              <span className={`tree-icon ${e.dir ? 'folder' : kind(e.name)}`} aria-hidden />
              <span className="tree-name">{e.name}</span>
            </button>
            {e.dir && isOpen && (
              <Level
                root={root}
                path={e.path} depth={depth + 1} listing={listing} open={open}
                query={query} toggle={toggle} onOpen={onOpen}
                onMention={onMention} activePath={activePath}
                draft={draft} setDraft={setDraft}
                onCreate={onCreate} onRename={onRename} onContext={onContext}
              />
            )}
          </div>
        )
      })}
      {shown.length === 0 && (
        <div className="tree-loading" style={{ paddingLeft: depth * 12 + 10 }}>
          {q ? `No matches` : 'Empty'}
        </div>
      )}
    </>
  )
}

/** Coarse file families, enough to make the tree scannable by shape. */
function kind(name: string) {
  const ext = name.slice(name.lastIndexOf('.') + 1).toLowerCase()
  if (['ts', 'tsx', 'js', 'jsx', 'mjs', 'cjs'].includes(ext)) return 'js'
  if (['py', 'rb', 'go', 'rs', 'java', 'c', 'cpp', 'h', 'swift', 'kt'].includes(ext)) return 'code'
  if (['json', 'toml', 'yaml', 'yml', 'ini', 'cfg', 'lock'].includes(ext)) return 'data'
  if (['md', 'txt', 'rst'].includes(ext)) return 'doc'
  if (['css', 'scss', 'html', 'svg'].includes(ext)) return 'web'
  if (['png', 'jpg', 'jpeg', 'gif', 'webp', 'ico', 'icns'].includes(ext)) return 'img'
  if (['sh', 'bash', 'zsh', 'ps1'].includes(ext)) return 'sh'
  return 'plain'
}
