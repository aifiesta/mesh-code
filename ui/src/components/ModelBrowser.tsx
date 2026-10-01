import { useMemo, useState, useRef, useEffect } from 'react'
import { fuzzyFilter, type Match } from '../fuzzy'
import { fmtCtx, fmtUsd, pricePerM, type CatalogModel } from '../types'
import { Highlight } from './Highlight'

type Sort = 'name' | 'cheapest' | 'context'

/** The live catalog is ~1000 models. Rendering every row on each keystroke
 *  is wasted work nobody scrolls through — you search instead. The cap is
 *  generous and the footer always states it, so it is a stated limit rather
 *  than a silent truncation. */
const RENDER_CAP = 200

/**
 * The catalog, browsable. This is the CLI's `/models` table plus the things
 * a table in a terminal could not do: filter as you type, sort by what you
 * actually care about, and act on a row.
 *
 * The two actions matter as much as the list. "Use" pins a model; "Never"
 * adds it to the router's exclude list — which the CLI could only ever build
 * implicitly, by blacklisting a model after it failed mid-session.
 */
export function ModelBrowser({ catalog, current, excluded, onPick, onExclude, onUnexclude, onRefresh }: {
  catalog: CatalogModel[]
  current: string
  excluded: string[]
  onPick: (id: string) => void
  onExclude: (id: string) => void
  onUnexclude: (id: string) => void
  /** Ask the engine to refetch the gateway's list (new models, new prices). */
  onRefresh: () => void
}) {
  const [q, setQ] = useState('')
  const [refreshing, setRefreshing] = useState(false)
  const [refreshedAt, setRefreshedAt] = useState<number | null>(null)
  // The catalog prop changing is the only "done" signal we get.
  const seen = useRef(catalog)
  useEffect(() => {
    if (catalog !== seen.current) {
      seen.current = catalog
      if (refreshing) { setRefreshing(false); setRefreshedAt(Date.now()) }
    }
  }, [catalog, refreshing])
  useEffect(() => {
    if (!refreshing) return
    const t = setTimeout(() => setRefreshing(false), 25_000)
    return () => clearTimeout(t)
  }, [refreshing])
  const [sort, setSort] = useState<Sort>('name')
  const [freeOnly, setFreeOnly] = useState(false)
  const [toolsOnly, setToolsOnly] = useState(false)

  const rows = useMemo(() => {
    const pre = catalog.filter((m) => {
      if (freeOnly && !m.is_free && pricePerM(m, 'prompt') !== 0) return false
      // A harness turn always sends tools, so a model that cannot call them
      // is not a useful pick here.
      if (toolsOnly && m.model_type && m.model_type !== 'text') return false
      return true
    })

    // Fuzzy, not substring: `gpt4m` has to reach `openai/gpt-4o-mini`.
    const hits = fuzzyFilter(q, pre, (m) => String(m.id))

    const byField = (a: CatalogModel, b: CatalogModel) => {
      if (sort === 'cheapest') {
        return (pricePerM(a, 'prompt') ?? Infinity) - (pricePerM(b, 'prompt') ?? Infinity)
      }
      if (sort === 'context') return (b.context_length ?? 0) - (a.context_length ?? 0)
      return String(a.id).localeCompare(String(b.id))
    }

    // With a query, match quality leads and the chosen sort breaks ties —
    // a cheap model that barely matches should never outrank an exact one.
    // With no query it is a plain browse, so the sort takes over entirely.
    return [...hits].sort((x, y) =>
      (q.trim() ? x.match.rank - y.match.rank : 0) || byField(x.item, y.item))
  }, [catalog, q, sort, freeOnly, toolsOnly])

  return (
    <div className="browser">
      <div className="browser-bar">
        <input
          className="browser-search"
          placeholder="Fuzzy search…  try “qw”, “gpt4m”, “son45”"
          value={q}
          autoFocus
          onChange={(e) => setQ(e.target.value)}
        />
        <div className="seg small">
          <button className={sort === 'name' ? 'on' : ''} onClick={() => setSort('name')}>A–Z</button>
          <button className={sort === 'cheapest' ? 'on' : ''} onClick={() => setSort('cheapest')}>Cheapest</button>
          <button className={sort === 'context' ? 'on' : ''} onClick={() => setSort('context')}>Context</button>
        </div>
        <label className="check">
          <input type="checkbox" checked={freeOnly} onChange={(e) => setFreeOnly(e.target.checked)} /> Free only
        </label>
        <label className="check">
          <input type="checkbox" checked={toolsOnly} onChange={(e) => setToolsOnly(e.target.checked)} /> Text only
        </label>
        <button className={`browser-refresh ${refreshing ? 'busy' : ''}`}
                title="Fetch the latest models and prices from Mesh API"
                disabled={refreshing}
                onClick={() => { setRefreshing(true); onRefresh() }}>
          <svg viewBox="0 0 16 16" width="13" height="13" aria-hidden fill="none"
               stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round">
            <path d="M13.5 8a5.5 5.5 0 0 1-9.6 3.7M2.5 8a5.5 5.5 0 0 1 9.6-3.7" />
            <path d="M12.5 1.8v2.7h-2.7M3.5 14.2v-2.7h2.7" />
          </svg>
          {refreshing ? 'Refreshing…' : 'Refresh'}
        </button>
      </div>

      {catalog.length === 0 && (
        <p className="browser-empty">
          The catalog hasn’t loaded. Check your API key and connection.
        </p>
      )}

      <div className="browser-head">
        <span>Model</span><span className="num">Context</span><span className="num">$/1M in</span>
        <span className="num">$/1M out</span><span />
      </div>
      <div className="browser-rows">
        {rows.slice(0, RENDER_CAP).map(({ item: m, match }: { item: CatalogModel; match: Match }) => {
          const id = String(m.id)
          const off = excluded.includes(id)
          return (
            <div className={`browser-row ${id === current ? 'current' : ''} ${off ? 'off' : ''}`} key={id}>
              <span className="m-id" title={id}>
                <Highlight text={id} positions={match.positions} />
                {m.supports_thinking && <span className="tag think" title="Supports a reasoning budget">think</span>}
                {id === current && <span className="tag now">in use</span>}
                {off && <span className="tag never">never</span>}
              </span>
              <span className="m-ctx num">{fmtCtx(m.context_length)}</span>
              <span className="num">{fmtUsd(pricePerM(m, 'prompt'))}</span>
              <span className="num">{fmtUsd(pricePerM(m, 'completion'))}</span>
              <span className="m-actions">
                {id !== current && <button className="use" onClick={() => onPick(id)}>Use</button>}
                {off
                  ? <button onClick={() => onUnexclude(id)}>Allow</button>
                  : <button className="danger" onClick={() => onExclude(id)}>Never</button>}
              </span>
            </div>
          )
        })}
      </div>
      <div className="browser-foot">
        {rows.length} of {catalog.length} models
        {refreshedAt && <> · updated {new Date(refreshedAt).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</>}
        {rows.length > RENDER_CAP && <> · showing the first {RENDER_CAP} — keep typing to narrow</>}
        {q.trim() && rows.length === 0 && <> — nothing matches “{q}”</>}
      </div>
    </div>
  )
}
