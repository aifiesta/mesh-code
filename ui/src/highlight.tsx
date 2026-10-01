import './prismManual'
import Prism from 'prismjs'
import 'prismjs/components/prism-markup'
import 'prismjs/components/prism-css'
import 'prismjs/components/prism-clike'
import 'prismjs/components/prism-javascript'
import 'prismjs/components/prism-typescript'
import 'prismjs/components/prism-jsx'
import 'prismjs/components/prism-tsx'
import 'prismjs/components/prism-python'
import 'prismjs/components/prism-json'
import 'prismjs/components/prism-bash'
import 'prismjs/components/prism-markdown'
import 'prismjs/components/prism-yaml'
import 'prismjs/components/prism-toml'
import 'prismjs/components/prism-go'
import 'prismjs/components/prism-rust'
import 'prismjs/components/prism-sql'

/**
 * Syntax colouring, rendered as React spans.
 *
 * Prism is used ONLY as a tokenizer: we never call Prism.highlight (which
 * returns an HTML string) and never set innerHTML. Every token becomes a
 * <span> with its text as a child, so model output and file contents keep
 * the same zero-injection guarantee the markdown renderer has.
 */
export type Span = { text: string; cls: string }

const BY_EXT: Record<string, string> = {
  html: 'markup', htm: 'markup', xml: 'markup', svg: 'markup', vue: 'markup',
  css: 'css', scss: 'css', less: 'css',
  js: 'javascript', mjs: 'javascript', cjs: 'javascript', jsx: 'jsx',
  ts: 'typescript', mts: 'typescript', cts: 'typescript', tsx: 'tsx',
  py: 'python', json: 'json', jsonc: 'json', md: 'markdown', mdx: 'markdown',
  yml: 'yaml', yaml: 'yaml', toml: 'toml', go: 'go', rs: 'rust', sql: 'sql',
  sh: 'bash', bash: 'bash', zsh: 'bash', ps1: 'bash',
}
const ALIAS: Record<string, string> = {
  html: 'markup', xml: 'markup', svg: 'markup', js: 'javascript',
  ts: 'typescript', py: 'python', sh: 'bash', shell: 'bash', zsh: 'bash',
  yml: 'yaml', rs: 'rust', golang: 'go',
}

/** Prism grammar name for a file name, or '' when we have none. */
export function langForFile(name: string): string {
  const base = name.split('/').pop() ?? name
  if (/^(Dockerfile|Makefile)$/i.test(base)) return 'bash'
  const ext = base.slice(base.lastIndexOf('.') + 1).toLowerCase()
  return BY_EXT[ext] ?? ''
}

/** Prism grammar name for a ```fence tag, or '' when we have none. */
export function langForTag(tag: string): string {
  const t = tag.toLowerCase()
  const name = ALIAS[t] ?? t
  return Prism.languages[name] ? name : ''
}

function flatten(tokens: (string | Prism.Token)[], parent: string, out: Span[]) {
  for (const t of tokens) {
    if (typeof t === 'string') { out.push({ text: t, cls: parent }); continue }
    const cls = parent ? `${parent} ${t.type}` : t.type
    const content = t.content
    if (typeof content === 'string') out.push({ text: content, cls })
    else if (Array.isArray(content)) flatten(content, cls, out)
    else flatten([content], cls, out)
  }
}

/** Tokenize the whole text (so block comments and template strings keep
 *  their context) and split the result into lines. Unknown language or an
 *  oversized file falls back to plain lines. */
export function highlightLines(text: string, lang: string): Span[][] {
  const grammar = lang ? Prism.languages[lang] : undefined
  if (!grammar || text.length > 400_000) {
    return text.split('\n').map((l) => (l ? [{ text: l, cls: '' }] : []))
  }
  const spans: Span[] = []
  flatten(Prism.tokenize(text, grammar), '', spans)
  const lines: Span[][] = [[]]
  for (const s of spans) {
    const parts = s.text.split('\n')
    parts.forEach((p, i) => {
      if (i > 0) lines.push([])
      if (p) lines[lines.length - 1].push({ text: p, cls: s.cls })
    })
  }
  return lines
}

/** `tag punctuation` -> `tk-tag tk-punctuation`. Prefixed because Prism's
 *  bare type names (`tag`, `title`, `chip`…) are also app class names — an
 *  HTML tag token once rendered as a small-caps badge. */
const tokenClass = (cls: string) => cls.split(' ').map((c) => `tk-${c}`).join(' ')

export function Line({ spans }: { spans: Span[] }) {
  if (!spans.length) return <>{' '}</>
  return (
    <>
      {spans.map((s, i) =>
        s.cls ? <span key={i} className={tokenClass(s.cls)}>{s.text}</span> : <span key={i}>{s.text}</span>)}
    </>
  )
}
