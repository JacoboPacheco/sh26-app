// FEATURE: "Watch the AI work" — the agent's run as a plain vertical timeline: what Gemini proposed (or which engine
// tool it called), what the engine found when it re-ran the case (red: still over its limit; green: holds), the
// findings sent back, the revision. Every row is a real step the backend recorded (solutions.py → report.agentic.trace,
// analyst.py → trace); nothing here is invented. Rows reveal one by one (reduced motion: all at once).
//
// Row shape (from the backend): {n, actor: 'gemini'|'engine', kind, tone: 'info'|'over'|'holds'|'muted',
//   title: string | {en, es}, detail?: string | {en, es}, call?: 'whatif_size(mw=1200)', ms?, cached?}
// A proposal or tool call ('propose' | 'revise' | 'call') followed by the engine's verdict ('verify' | 'result') is
// shown as one item: the proposal, then the verdict under it a beat later.
//
// Props: trace (rows) · lang 'en'|'es' · animate (reveal step by step) · stepMs · shown (items to show, when a
// clock outside drives it — the presentation) · max (keep at most this many items: the show's short version) ·
// heading (false: none) · badge (an <AiBadge/> for the header) · totals (a line under the list) · live (rows are
// still arriving: a "working" line under them) · onDone (called once every item is shown) · follow (keep the newest
// item in view: the list scrolls itself) · brief (proposals without their detail lines: the show) · className
import { useEffect, useMemo, useRef, useState } from 'react'
import './ai.css'
import { compactItems, groupTrace, loc } from './trace'

const T = {
  en: { heading: 'Watch the AI work', gemini: 'Gemini', engine: 'Engine', working: 'Working… each step appears as it happens.', open: 'Watch the AI work', hide: 'Hide the AI’s work', cached: 'cached', round: 'round' },
  es: { heading: 'Mira trabajar a la IA', gemini: 'Gemini', engine: 'Motor', working: 'Trabajando… cada paso aparece cuando ocurre.', open: 'Mira trabajar a la IA', hide: 'Ocultar el trabajo de la IA', cached: 'en caché', round: 'ronda' },
}

function useReduced() {
  const [r] = useState(() => {
    try {
      return window.matchMedia('(prefers-reduced-motion: reduce)').matches
    } catch {
      return false
    }
  })
  return r
}

export default function AgentTrace({ trace, lang = 'en', animate = true, stepMs = 650, shown, max, heading, badge, totals, live = false, onDone, follow = false, brief = false, className }) {
  const t = T[lang] || T.en
  const items = useMemo(() => compactItems(groupTrace(trace || []), max), [trace, max])
  const reduced = useReduced()
  const moving = animate && !reduced
  const [count, setCount] = useState(moving ? 0 : items.length)
  // reveal one item per step, catching up as rows arrive (live); without motion everything is there at once
  useEffect(() => {
    if (shown != null || !moving || count >= items.length) return undefined
    const id = setTimeout(() => setCount((c) => Math.min(items.length, c + 1)), count === 0 ? 120 : stepMs)
    return () => clearTimeout(id)
  }, [shown, moving, count, items.length, stepMs])
  const n = shown != null ? Math.max(0, Math.min(shown, items.length)) : moving ? count : items.length
  const done = n >= items.length
  const root = useRef(null)
  // follow: the newest item stays in view as it appears (the list scrolls itself when it is a scroll box, else the
  // page or panel around it scrolls just enough)
  useEffect(() => {
    const el = root.current
    if (!follow || !el || !n) return
    const behavior = moving ? 'smooth' : 'auto'
    if (el.scrollHeight > el.clientHeight + 2) el.scrollTo({ top: el.scrollHeight, behavior })
    else el.querySelector('.agt__list > li:last-child')?.scrollIntoView?.({ block: 'nearest', behavior })
  }, [follow, n, moving])
  const doneRef = useRef(onDone)
  useEffect(() => {
    doneRef.current = onDone
  })
  useEffect(() => {
    if (done && items.length && !live) doneRef.current?.()
  }, [done, items.length, live])

  return (
    <section ref={root} className={`agt${moving ? ' agt--anim' : ''}${brief ? ' agt--brief' : ''}${className ? ` ${className}` : ''}`} aria-label={heading || t.heading} style={{ '--step': `${stepMs}ms` }}>
      {heading !== false && (
        <header className="agt__head">
          <h3 className="agt__h">{heading || t.heading}</h3>
          {badge}
        </header>
      )}
      <ol className="agt__list">
        {items.slice(0, n).map((it) => (
          <Item key={it.key} it={it} t={t} lang={lang} />
        ))}
      </ol>
      {live && done && (
        <p className="agt__working" role="status">
          {t.working}
        </p>
      )}
      {totals && done && !live && <p className="agt__totals">{totals}</p>}
    </section>
  )
}

function Item({ it, t, lang }) {
  const h = it.head
  const v = it.verdict
  const detail = loc(h.detail, lang)
  return (
    <li className={`agt__item agt__item--${h.actor} agt__item--${h.kind} agt__tone--${h.tone || 'info'}${v ? ' agt__item--pair' : ''}`}>
      <span className={`agt__who agt__who--${h.actor}`}>{h.actor === 'gemini' ? t.gemini : t.engine}</span>
      <div className="agt__body">
        <p className="agt__title">{loc(h.title, lang)}</p>
        {h.call && <code className="agt__call">{h.call}</code>}
        {detail && <p className="agt__detail">{detail}</p>}
        {v && (
          <div className={`agt__verdict agt__tone--${v.tone || 'info'}`}>
            <span className={`agt__who agt__who--${v.actor}`}>{v.actor === 'gemini' ? t.gemini : t.engine}</span>
            <div className="agt__body">
              <p className="agt__title">{loc(v.title, lang)}</p>
              {loc(v.detail, lang) && <p className="agt__detail">{loc(v.detail, lang)}</p>}
            </div>
            {(v.ms != null || v.cached) && <span className="agt__ms">{v.cached ? t.cached : `${Math.max(1, v.ms)} ms`}</span>}
          </div>
        )}
      </div>
    </li>
  )
}

// A "Watch the AI work" button that opens the trace (it plays when opened). Nothing renders without a trace.
export function AgentTraceToggle({ trace, lang = 'en', label, ...rest }) {
  const t = T[lang] || T.en
  const [open, setOpen] = useState(false)
  const box = useRef(null)
  // opened: bring the trace into view (it opens under the button, often below the fold)
  useEffect(() => {
    if (open) box.current?.querySelector('.agt')?.scrollIntoView?.({ block: 'nearest' })
  }, [open])
  if (!trace?.length) return null
  return (
    <div className="agt-toggle" ref={box}>
      <button type="button" className="agt-toggle__btn" aria-expanded={open} onClick={() => setOpen((v) => !v)}>
        {open ? t.hide : label || t.open}
      </button>
      {open && <AgentTrace trace={trace} lang={lang} {...rest} />}
    </div>
  )
}
