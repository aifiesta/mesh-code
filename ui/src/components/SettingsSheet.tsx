import { useEffect, useRef, useState } from 'react'
import type { CatalogModel, LocalStatus, SettingsDoc } from '../types'
import { Field } from './Field'
import type { SettingSpec } from '../types'
import { LocalModelPanel } from './LocalModelPanel'
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
export function SettingsSheet({ settings, catalog, local, localActions, notices, onDismissNotice, error, onSet, onClose, onRefreshCatalog, initialTab }: {
  settings: SettingsDoc | null
  catalog: CatalogModel[]
  local: LocalStatus | null
  localActions: {
    install: (model: string) => void; cancel: (job: string) => void; remove: (model: string) => void
    setDefault: (model: string) => void; server: (action: 'stop' | 'restart') => void; refresh: () => void
  }
  /** App-level notices: local-layer failures land here while the sheet is open. */
  notices: { id: string; level: string; text: string }[]
  onDismissNotice: (id: string) => void
  error: { key: string; message: string } | null
  onSet: (key: string, value: any) => void
  onClose: () => void
  onRefreshCatalog: (force?: boolean) => void
  initialTab?: string
}) {
  const [tab, setTab] = useState<string>(initialTab ?? settings?.categories[0]?.id ?? 'model')
  useEffect(() => { if (initialTab) setTab(initialTab) }, [initialTab])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  // Refresh the catalog once per visit to the Model tab. The tab, not the
  // callback identity, is the trigger: App passes an inline arrow.
  const refreshed = useRef<string | null>(null)
  useEffect(() => {
    if (tab !== 'model') { refreshed.current = null; return }
    if (refreshed.current === tab) return
    refreshed.current = tab
    onRefreshCatalog()
  }, [tab, onRefreshCatalog])

  // A placeholder, so ⌘, before the settings doc arrives is not a dead shortcut.
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
  const depText = (doc: SettingsDoc, dep?: Record<string, any>) =>
    Object.entries(dep ?? {}).map(([k, v]) => {
      const spec = doc.settings.find((s) => s.key === k)
      const opt = spec?.options?.find((o) => o.value === v)
      return `${spec?.label ?? k} is ${opt?.label ?? String(v)}`
    }).join(' and ')

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
          {notices.map((n) => (
            <div className={`notice ${n.level} app-notice`} key={n.id}>
              <span>{n.text}</span>
              <button className="link" onClick={() => onDismissNotice(n.id)}>Dismiss</button>
            </div>
          ))}

          {tab === 'model' && (
            <ModelBrowser
              catalog={catalog}
              current={String(current)}
              excluded={excluded}
              onPick={(id) => onSet('model', id)}
              onExclude={(id) => onSet('exclude_models', [...excluded, id])}
              onUnexclude={(id) => onSet('exclude_models', excluded.filter((x) => x !== id))}
              onRefresh={() => onRefreshCatalog(true)}
              onInstall={localActions.install}
            />
          )}

          {tab === 'local' && (
            local
              ? <LocalModelPanel
                  local={local}
                  onInstall={localActions.install}
                  onCancel={localActions.cancel}
                  onDelete={localActions.remove}
                  onSetDefault={localActions.setDefault}
                  onServer={localActions.server}
                  onRefresh={localActions.refresh}
                />
              : <p className="sheet-blurb">The local model layer has not reported yet.</p>
          )}

          {inTab
            .filter((s) => s.key !== 'model' && s.key !== 'local_model')
            .map((s) => (
              <div key={s.key} className={met(s.depends_on) ? '' : 'field-muted'}>
                {s.type === 'model'
                  ? <CloudModelField spec={s} catalog={catalog} onChange={onSet} />
                  : <Field spec={s} onChange={onSet} />}
                {!met(s.depends_on) && (
                  <p className="field-dep">
                    Applies when {depText(settings, s.depends_on)}.
                  </p>
                )}
              </div>
            ))}
        </div>
      </div>
    </div>
  )
}

/** A model-typed setting other than the pinned one: a plain picker over the
 *  cloud catalog (local rows are not cloud fallbacks). */
function CloudModelField({ spec, catalog, onChange }: {
  spec: SettingSpec
  catalog: CatalogModel[]
  onChange: (key: string, value: any) => void
}) {
  const value = String(spec.value ?? spec.default ?? '')
  const ids = catalog.filter((m) => !m.local).map((m) => String(m.id)).sort()
  if (value && !ids.includes(value)) ids.unshift(value)
  const none = value === '' || spec.default === ''
  return (
    <div className="field">
      <div className="field-head">
        <label htmlFor={`f-${spec.key}`}>{spec.label}</label>
      </div>
      {spec.help && <p className="field-help">{spec.help}</p>}
      <div className="field-control">
        <select id={`f-${spec.key}`} value={value} onChange={(e) => onChange(spec.key, e.target.value)}>
          {none && <option value="">No fallback (fail instead)</option>}
          {ids.map((id) => <option key={id} value={id}>{id}</option>)}
        </select>
      </div>
      {spec.why && <p className="field-why">{spec.why}</p>}
    </div>
  )
}
