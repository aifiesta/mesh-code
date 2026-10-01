/**
 * Fuzzy matching, ported from the CLI's completer.py.
 *
 * The ranking is the CLI's exactly — prefix > substring > subsequence — so
 * muscle memory transfers: `qw` lists every qwen model, and `gpt4m` finds
 * `openai/gpt-4o-mini` because the letters appear in order even though they
 * are not adjacent. That last case is the whole point; a plain
 * `includes()` filter finds nothing for `gpt4m`.
 *
 * This version additionally returns WHICH characters matched, so the UI can
 * highlight them. In a terminal menu you could see why a row matched from
 * the ordering alone; in a long scrolling list you cannot, and an
 * unexplained match reads like a bug.
 */

export const enum Rank {
  Prefix = 0,
  Substring = 1,
  Subsequence = 2,
}

export type Match = { rank: Rank; positions: number[] }

export function fuzzyMatch(query: string, candidate: string): Match | null {
  const q = query.toLowerCase().trim()
  const c = candidate.toLowerCase()
  if (!q) return { rank: Rank.Prefix, positions: [] }

  if (c.startsWith(q)) {
    return { rank: Rank.Prefix, positions: range(0, q.length) }
  }

  const at = c.indexOf(q)
  if (at >= 0) {
    return { rank: Rank.Substring, positions: range(at, at + q.length) }
  }

  // Subsequence: walk the candidate once, consuming query characters in
  // order. Greedy-leftmost, matching the CLI's `all(ch in it for ch in q)`.
  const positions: number[] = []
  let qi = 0
  for (let ci = 0; ci < c.length && qi < q.length; ci++) {
    if (c[ci] === q[qi]) {
      positions.push(ci)
      qi++
    }
  }
  return qi === q.length ? { rank: Rank.Subsequence, positions } : null
}

/** Filter + rank, preserving the caller's own tie-break order. */
export function fuzzyFilter<T>(
  query: string,
  items: T[],
  key: (item: T) => string,
): { item: T; match: Match }[] {
  const out: { item: T; match: Match }[] = []
  for (const item of items) {
    const match = fuzzyMatch(query, key(item))
    if (match) out.push({ item, match })
  }
  return out
}

function range(a: number, b: number): number[] {
  const out: number[] = []
  for (let i = a; i < b; i++) out.push(i)
  return out
}
