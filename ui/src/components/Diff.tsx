import { useMemo } from 'react'

/**
 * A line diff good enough to decide "do I approve this write?".
 *
 * Deliberately not a full Myers diff: this runs on a modal that must open
 * instantly, and the question it answers is "what is about to change",
 * not "what is the minimal edit script". Common prefix and suffix are
 * trimmed, and whatever is left in the middle is shown as removed-then-added.
 */
type Row = { kind: 'ctx' | 'add' | 'del'; n1: number | null; n2: number | null; text: string }

function diffRows(oldText: string, newText: string): Row[] {
  const a = oldText ? oldText.split('\n') : []
  const b = newText ? newText.split('\n') : []

  let head = 0
  while (head < a.length && head < b.length && a[head] === b[head]) head++
  let tail = 0
  while (
    tail < a.length - head &&
    tail < b.length - head &&
    a[a.length - 1 - tail] === b[b.length - 1 - tail]
  ) tail++

  const rows: Row[] = []
  const CTX = 3
  const from = Math.max(0, head - CTX)
  for (let i = from; i < head; i++) rows.push({ kind: 'ctx', n1: i + 1, n2: i + 1, text: a[i] })
  for (let i = head; i < a.length - tail; i++) rows.push({ kind: 'del', n1: i + 1, n2: null, text: a[i] })
  for (let i = head; i < b.length - tail; i++) rows.push({ kind: 'add', n1: null, n2: i + 1, text: b[i] })
  const endStop = Math.min(tail, CTX)
  for (let i = 0; i < endStop; i++) {
    const ai = a.length - tail + i
    rows.push({ kind: 'ctx', n1: ai + 1, n2: b.length - tail + i + 1, text: a[ai] })
  }
  return rows
}

export function Diff({ oldText, newText, exists }: {
  oldText: string; newText: string; exists: boolean
}) {
  const rows = useMemo(
    () => (exists ? diffRows(oldText, newText)
                  : newText.split('\n').map((t, i): Row => ({ kind: 'add', n1: null, n2: i + 1, text: t }))),
    [oldText, newText, exists])

  const added = rows.filter((r) => r.kind === 'add').length
  const removed = rows.filter((r) => r.kind === 'del').length

  return (
    <div className="diff">
      <div className="diff-head">
        <span className="diff-stat add">+{added}</span>
        <span className="diff-stat del">−{removed}</span>
        <span className="diff-note">{exists ? 'overwrites an existing file' : 'new file'}</span>
      </div>
      <div className="diff-body">
        {rows.map((r, i) => (
          <div key={i} className={`diff-row ${r.kind}`}>
            <span className="ln">{r.n1 ?? ''}</span>
            <span className="ln">{r.n2 ?? ''}</span>
            <span className="sign">{r.kind === 'add' ? '+' : r.kind === 'del' ? '−' : ' '}</span>
            <span className="code">{r.text || ' '}</span>
          </div>
        ))}
      </div>
    </div>
  )
}
