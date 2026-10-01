import { useEffect, useRef, useState } from 'react'
import type { CatalogModel, SettingsDoc } from '../types'
import { Field } from './Field'
import { ModelBrowser } from './ModelBrowser'

/**
 * The full surface, grouped and explained. Every control — including ones
 * added to the engine after this file was written — comes from the schema.
 *
 * A setting whose `depends_on` is unmet is dimmed rather than hidden: the
 * routing weights only apply in smart mode, and seeing them greyed out tells
 * you smart mode exists. Hiding them just makes the feature invisible again,
 * which is the problem this whole screen is meant to solve.
 */
export function SettingsSheet({ settings, catalog, error, onSet, onClose, onRefreshCatalog }: {
  settings: SettingsDoc | null
  catalog: CatalogModel[]
  error: { key: string; message: string } | null
  onSet: (key: string, value: any) => void
  onClose: () => void
  onRefreshCatalog: (force?: boolean) => void
}) {
  const [tab, setTab] = useState<string>(settings?.categories[0]?.id ?? 'model')

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  // Refresh the catalog ONCE per visit to the Model tab.
  //
  // This depended on `onRefreshCatalog`, which App passes as an inline arrow
  // — a new function identity on every render. So: effect fires, request
  // returns, catalog lands in state, App re-renders, identity changes,
  // effect fires again… an unbounded request loop against the engine for as
  // long as the tab was open. The ref makes the TAB the trigger, which is
  // what it was always meant to be.
  const refreshed = useRef<string | null>(null)
  useEffect(() => {
    if (tab !== 'model') { refreshed.current = null; return }
    if (refreshed.current === tab) return
    refreshed.current = tab
    onRefreshCatalog()
  }, [tab, onRefreshCatalog])

  // Show a placeholder rather than rendering NOTHING: pressing ⌘, before the
  // settings doc arrives used to open an invisible sheet (state set, nothing
  // drawn), reading as a dead shortcut.
  if (!settings) {
    return (
      <div className="modal-scrim" onClick={onClose}>
        <div className="sheet" onClick={(e) => e.stopPropagation()} role="dialog" aria-modal="true">
          <div className="sheet-empty">Waiting for the engine…</div>
        </div>
      </div>
    )
  }

  const current = settings.settings.find((s) => s.key === 'model')?.value ?? ''
  const excluded = (settings.settings.find((s) => s.key === 'exclude_models')?.value ?? []) as string[]
  const inTab = settings.settings.filter((s) => s.category === tab)
  const cat = settings.categories.find((c) => c.id === tab)

  const met = (dep?: Record<string, any>) =>
    !dep || Object.entries(dep).every(([k, v]) =>
      settings.settings.find((s) => s.key === k)?.value === v)

  return (
    <div className="modal-scrim" onClick={onClose}>
      <div className="sheet" onClick={(e) => e.stopPropagation()} role="dialog" aria-modal="true">
        <nav className="sheet-nav">
          <h2>Settings</h2>
          {settings.categories.map((c) => (
            <button key={c.id} className={c.id === tab ? 'on' : ''} onClick={() => setTab(c.id)}>
              {c.label}
            </button>
          ))}
          <div className="spacer" />
          <button className="btn ghost" onClick={onClose}>Done <kbd>esc</kbd></button>
        </nav>

        <div className="sheet-body">
          {cat && <p className="sheet-blurb">{cat.blurb}</p>}
          {error && (
            <div className="sheet-error">
              <strong>{error.key}</strong> — {error.message}
            </div>
          )}

          {tab === 'model' && (
            <ModelBrowser
              catalog={catalog}
              current={String(current)}
              excluded={excluded}
              onPick={(id) => onSet('model', id)}
              onExclude={(id) => onSet('exclude_models', [...excluded, id])}
              onUnexclude={(id) => onSet('exclude_models', excluded.filter((x) => x !== id))}
              onRefresh={() => onRefreshCatalog(true)}
            />
          )}

          {inTab
            .filter((s) => s.type !== 'model')
            .map((s) => (
              <div key={s.key} className={met(s.depends_on) ? '' : 'field-muted'}>
                <Field spec={s} onChange={onSet} />
                {!met(s.depends_on) && (
                  <p className="field-dep">
                    Applies when routing is set to Smart.
                  </p>
                )}
              </div>
            ))}
        </div>
      </div>
    </div>
  )
}
