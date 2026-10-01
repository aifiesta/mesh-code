import { useEffect, useMemo, useRef, useState } from 'react'
import { fuzzyMatch } from '../fuzzy'
import type { SettingsDoc, SettingSpec } from '../types'

export type Action = {
  id: string
  title: string
  hint?: string
  group: string
  /** Extra words that should match this action, so searching for what you
   *  WANT finds it even when you don't know what it's called. */
  keywords?: string
  run: () => void
}

/**
 * ⌘K. The answer to "there are 24 commands and I can't find any of them".
 *
 * Two things make it work better than the slash commands it replaces.
 * First, every enum setting is expanded into one action PER OPTION —
 * "Routing: Smart (local)" is directly runnable, so you never have to learn
 * that routing has three values before you can change it. Second, matching
 * runs over the engine's own help text, so typing "cheap" finds token
 * savings and smart routing without either of them containing the word.
 */
export function CommandPalette({ settings, extra, onSet, onClose }: {
  settings: SettingsDoc | null
  extra: Action[]
  onSet: (key: string, value: any) => void
  onClose: () => void
}) {
  const [q, setQ] = useState('')
  const [sel, setSel] = useState(0)
  const listRef = useRef<HTMLDivElement>(null)

  const actions = useMemo<Action[]>(() => {
    const out: Action[] = [...extra]
    for (const spec of settings?.settings ?? []) {
      const group = groupLabel(settings, spec)
      if (spec.type === 'enum') {
        for (const o of spec.options ?? []) {
          out.push({
            id: `${spec.key}:${String(o.value)}`,
            title: `${spec.label}: ${o.label}`,
            hint: o.blurb || spec.help,
            group,
            keywords: `${spec.key} ${spec.help ?? ''} ${spec.why ?? ''}`,
            run: () => onSet(spec.key, o.value),
          })
        }
      } else if (spec.type === 'bool') {
        out.push({
          id: `${spec.key}:toggle`,
          title: `${spec.label}: ${spec.value ? 'turn off' : 'turn on'}`,
          hint: spec.help,
          group,
          keywords: `${spec.key} ${spec.why ?? ''}`,
          run: () => onSet(spec.key, !spec.value),
        })
      }
    }
    return out
  }, [settings, extra, onSet])

  // The engine's own category order, with the app's actions ahead of every
  // setting — "open a project" is a bigger move than "writing style".
  const groupRank = useMemo(() => {
    const rank = new Map<string, number>()
    for (const a of extra) if (!rank.has(a.group)) rank.set(a.group, rank.size)
    const base = rank.size
    ;(settings?.categories ?? []).forEach((c, i) => {
      if (!rank.has(c.label)) rank.set(c.label, base + i)
    })
    return rank
  }, [extra, settings])

  /**
   * Ranked AND grouped, in that order of priority.
   *
   * Previously this was a flat relevance sort with each row printing its own
   * category in a gutter — so "Limits & recovery" was repeated on every row,
   * wrapped to two lines, and blew the row heights apart. Now results stay
   * contiguous by group, and the GROUPS are ordered by their best-scoring
   * member: what you searched for still surfaces first, it just arrives with
   * its siblings and one header instead of five copies of a label.
   */
  const hits = useMemo(() => {
    const needle = q.trim().toLowerCase()
    let scored: { a: Action; score: number }[]
    if (!needle) {
      scored = actions.map((a) => ({ a, score: 0 }))
    } else {
      const terms = needle.split(/\s+/)
      scored = actions
        .map((a) => {
          const hay = `${a.title} ${a.hint ?? ''} ${a.keywords ?? ''} ${a.group}`.toLowerCase()
          if (terms.every((t) => hay.includes(t))) {
            // Title matches outrank help-text matches, so exact intent wins.
            return { a, score: a.title.toLowerCase().includes(needle) ? 0 : 1 }
          }
          // Fall back to a fuzzy title match so abbreviations land too —
          // `rtsm` reaches "Routing: Smart (local)".
          const m = fuzzyMatch(needle, a.title)
          return m ? { a, score: 2 + m.rank } : null
        })
        .filter(Boolean) as { a: Action; score: number }[]
    }

    const best = new Map<string, number>()
    for (const { a, score } of scored) {
      const cur = best.get(a.group)
      if (cur === undefined || score < cur) best.set(a.group, score)
    }
    return scored
      .sort((x, y) =>
        (best.get(x.a.group)! - best.get(y.a.group)!) ||
        ((groupRank.get(x.a.group) ?? 99) - (groupRank.get(y.a.group) ?? 99)) ||
        (x.score - y.score))
      .map((x) => x.a)
  }, [actions, q, groupRank])

  /**
   * Contiguous runs of one group, each rendered inside its own box.
   *
   * The wrapper is what makes the sticky header work: a sticky element is
   * bounded by its CONTAINING BLOCK, so a section per group means each
   * header pins while its own rows scroll past and is then pushed out by
   * the next one. Rendered flat (no wrapper), every header instead pins to
   * the same offset and they silently pile up on top of each other; wrapped
   * per ROW, the containing block is one row tall and nothing pins at all.
   */
  const sections = useMemo(() => {
    const out: { group: string; items: { a: Action; i: number }[] }[] = []
    hits.forEach((a, i) => {
      const last = out[out.length - 1]
      if (last && last.group === a.group) last.items.push({ a, i })
      else out.push({ group: a.group, items: [{ a, i }] })
    })
    return out
  }, [hits])

  useEffect(() => setSel(0), [q])
  useEffect(() => {
    listRef.current?.querySelector('.pal-row.on')?.scrollIntoView({ block: 'nearest' })
  }, [sel])

  return (
    <div className="modal-scrim palette-scrim" onClick={onClose}>
      <div className="palette" onClick={(e) => e.stopPropagation()}>
        <input
          className="palette-input"
          autoFocus
          placeholder="Search settings and actions…"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'ArrowDown') { e.preventDefault(); setSel((i) => Math.min(i + 1, hits.length - 1)) }
            if (e.key === 'ArrowUp') { e.preventDefault(); setSel((i) => Math.max(i - 1, 0)) }
            if (e.key === 'Enter' && hits[sel]) { hits[sel].run(); onClose() }
            if (e.key === 'Escape') onClose()
          }}
        />
        <div className="palette-list" ref={listRef}>
          {hits.length === 0 && <div className="palette-empty">Nothing matches “{q}”.</div>}
          {sections.map((sec, n) => (
            <div className="pal-section" key={`${sec.group}-${n}`}>
              <div className="pal-group-head">{sec.group}</div>
              {sec.items.map(({ a, i }) => (
                <button
                  key={a.id}
                  className={`pal-row ${i === sel ? 'on' : ''}`}
                  onMouseEnter={() => setSel(i)}
                  onClick={() => { a.run(); onClose() }}
                >
                  <span className="pal-title">{a.title}</span>
                  {a.hint && <span className="pal-hint">{a.hint}</span>}
                </button>
              ))}
            </div>
          ))}
        </div>
        <div className="palette-foot">
          <kbd>↑↓</kbd> navigate <kbd>⏎</kbd> run <kbd>esc</kbd> close
          <span className="spacer" />
          <span>{hits.length} result{hits.length === 1 ? '' : 's'}</span>
        </div>
      </div>
    </div>
  )
}

function groupLabel(doc: SettingsDoc | null, spec: SettingSpec) {
  return doc?.categories.find((c) => c.id === spec.category)?.label ?? 'Settings'
}
