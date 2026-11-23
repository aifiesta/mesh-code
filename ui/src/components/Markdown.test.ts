import { describe, expect, it } from 'vitest'
import { parse } from './Markdown'

describe('markdown tables', () => {
  it('parses a pipe table with alignment and escaped pipes', () => {
    const src = [
      'Intro line',
      '| File | Size | Purpose |',
      '|:-----|-----:|---------|',
      '| `a.js` | 1 KB | logic \\| more |',
      '| b.js | 2 KB |',
      '',
      'After',
    ].join('\n')
    const blocks = parse(src)
    expect(blocks.map((b) => b.t)).toEqual(['p', 'table', 'p'])
    const t = blocks[1] as Extract<ReturnType<typeof parse>[number], { t: 'table' }>
    expect(t.head).toEqual(['File', 'Size', 'Purpose'])
    expect(t.align).toEqual(['left', 'right', null])
    expect(t.rows).toEqual([['`a.js`', '1 KB', 'logic | more'], ['b.js', '2 KB']])
  })

  it('leaves a lone pipe line as prose', () => {
    expect(parse('| not a table |\nplain').map((b) => b.t)).toEqual(['p'])
  })

  it('never hangs on short, mixed or half-streamed delimiter rows', () => {
    for (const md of ['| a | b |\n|---|:-:|', '| a | b |\n| --- | -- |', '| a | b |\n|---+---|',
                      '| a | b |\n|---|-', '| a | b |\n|:---:|:']) {
      expect(parse(md).length).toBeGreaterThan(0)
    }
    expect(parse('| a | b |\n|---|:-:|\n| 1 | 2 |').map((b) => b.t)).toEqual(['table'])
  })
})
