// Must be evaluated BEFORE prismjs: with `manual` set, Prism does not scan
// the document for <code> blocks on load. Its own module imports nothing,
// so import order alone guarantees this runs first.
;(globalThis as any).Prism = { manual: true }
export {}
