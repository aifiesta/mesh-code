import { IconX } from './Chevron'
import { useEffect, useState } from 'react'
import type { SettingSpec } from '../types'

/**
 * Renders ONE setting from the engine's schema.
 *
 * Nothing here knows what "route_mode" or "optimize" mean — it dispatches on
 * `type` and shows the label, help and rationale the engine supplied. That
 * is what keeps three separate surfaces (control bar, palette, settings
 * sheet) consistent, and what lets a new engine setting appear in the UI
 * without a matching frontend change.
 */
export function Field({ spec, onChange, compact, label }: {
  spec: SettingSpec
  onChange: (key: string, value: any) => void
  /** Popover form: a small label and the control, nothing else. The
   *  help and rationale still exist — as the label's tooltip — but a
   *  popover that repeats the settings sheet's prose is a settings sheet. */
  compact?: boolean
  /** Override the engine's label (the popover already says "Routing"). */
  label?: string
}) {
  const set = (v: any) => onChange(spec.key, v)

  if (compact) {
    return (
      <div className="field compact">
        <div className="field-head">
          <label htmlFor={`f-${spec.key}`} title={[spec.help, spec.why].filter(Boolean).join('\n\n')}>
            {label ?? spec.label}
          </label>
          {spec.beta && <span className="tag beta">beta</span>}
        </div>
        <div className="field-control">{control(spec, set)}</div>
      </div>
    )
  }

  return (
    <div className="field">
      <div className="field-head">
        <label htmlFor={`f-${spec.key}`}>{spec.label}</label>
        {spec.beta && <span className="tag beta">beta</span>}
      </div>
      {spec.help && <p className="field-help">{spec.help}</p>}
      <div className="field-control">{control(spec, set)}</div>
      {spec.why && <p className="field-why">{spec.why}</p>}
    </div>
  )
}

function control(spec: SettingSpec, set: (v: any) => void) {
  const v = spec.value ?? spec.default

  switch (spec.type) {
    case 'enum':
      return (
        <div className="seg" role="radiogroup" aria-label={spec.label}>
          {(spec.options ?? []).map((o) => (
            <button
              key={String(o.value)}
              role="radio"
              aria-checked={o.value === v}
              className={o.value === v ? 'on' : ''}
              title={o.blurb}
              onClick={() => set(o.value)}
            >
              {o.label}
            </button>
          ))}
        </div>
      )

    case 'bool':
      return (
        <button
          className={`toggle ${v ? 'on' : ''}`}
          role="switch"
          aria-checked={!!v}
          onClick={() => set(!v)}
        >
          <span className="knob" />
          <span className="toggle-text">{v ? 'On' : 'Off'}</span>
        </button>
      )

    case 'dial':
      return (
        <div className="dial">
          <input
            id={`f-${spec.key}`}
            type="range"
            min={spec.min ?? 0} max={spec.max ?? 1} step={spec.step ?? 0.05}
            value={Number(v) || 0}
            onChange={(e) => set(Number(e.target.value))}
          />
          <span className="dial-value">
            {Number(v) > 0 ? `${Math.round(Number(v) * 100)}%` : 'Off'}
          </span>
        </div>
      )

    case 'int':
      return <IntField spec={spec} value={Number(v) || 0} onCommit={set} />

    case 'weights':
      return <Weights value={v ?? {}} onChange={set} />

    case 'text':
      return spec.multiline ? (
        <textarea id={`f-${spec.key}`} rows={4} defaultValue={String(v ?? '')}
                  onBlur={(e) => set(e.target.value)} />
      ) : (
        <input id={`f-${spec.key}`} type="text" defaultValue={String(v ?? '')}
               onBlur={(e) => set(e.target.value)} />
      )

    case 'model_list':
      return <ModelList value={Array.isArray(v) ? v : []} onChange={set} />

    default:
      return null
  }
}

/**
 * The routing balance. Shown as three sliders that always sum to 100%, since
 * only the RATIO matters to the router — an absolute value would imply a
 * precision the weights do not have.
 */
function Weights({ value, onChange }: {
  value: Record<string, number>
  onChange: (v: Record<string, number>) => void
}) {
  const keys = ['cost', 'cap', 'speed'] as const
  const LABEL = { cost: 'Cheaper', cap: 'More capable', speed: 'Faster' }
  const total = keys.reduce((a, k) => a + (Number(value[k]) || 0), 0) || 1

  return (
    <div className="weights">
      {keys.map((k) => {
        const pct = Math.round(((Number(value[k]) || 0) / total) * 100)
        return (
          <div className="weight-row" key={k}>
            <span className="weight-label">{LABEL[k]}</span>
            <input
              type="range" min={0} max={100} value={pct}
              onChange={(e) => onChange({ ...value, [k]: Number(e.target.value) / 100 })}
            />
            <span className="weight-pct">{pct}%</span>
          </div>
        )
      })}
    </div>
  )
}

function ModelList({ value, onChange }: {
  value: string[]
  onChange: (v: string[]) => void
}) {
  const [draft, setDraft] = useState('')
  return (
    <div className="model-list">
      {value.length === 0 && <span className="muted">None</span>}
      {value.map((m) => (
        <span className="chip" key={m}>
          {m}
          <button onClick={() => onChange(value.filter((x) => x !== m))}><IconX size={10} /></button>
        </span>
      ))}
      <input
        placeholder="add model id…"
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && draft.trim()) {
            onChange([...value, draft.trim()]); setDraft('')
          }
        }}
      />
    </div>
  )
}


/**
 * Integer settings commit on blur or Enter, not per keystroke. Saving every
 * keystroke meant typing "20" first persisted 2 — and Chromium also steps a
 * focused number input on mouse-wheel, so scrolling the settings sheet past
 * "Max steps per turn" could silently cap every turn at a handful of hops.
 * Wheel now blurs the field instead of changing it.
 */
function IntField({ spec, value, onCommit }: {
  spec: SettingSpec; value: number; onCommit: (v: number) => void
}) {
  const [draft, setDraft] = useState(String(value))
  useEffect(() => { setDraft(String(value)) }, [value])
  const commit = () => {
    const n = Math.max(spec.min ?? 0, Math.min(spec.max ?? 999, Math.round(Number(draft) || 0)))
    setDraft(String(n))
    if (n !== value) onCommit(n)
  }
  return (
    <div className="stepper">
      <input
        id={`f-${spec.key}`}
        type="number"
        inputMode="numeric"
        min={spec.min ?? 0} max={spec.max ?? 999}
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); (e.target as HTMLInputElement).blur() } }}
        onWheel={(e) => (e.target as HTMLInputElement).blur()}
      />
      {value === 0 && spec.zero_label &&
        <span className="stepper-note">{spec.zero_label}</span>}
    </div>
  )
}
