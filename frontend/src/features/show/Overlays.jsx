import { useEffect, useMemo, useRef, useState } from 'react'
import { fmtValue, parts } from './format'
import { actorName, atMs, mapBusy, spanned, useTicks } from './util'

// The scene's HTML layers over the map: counters, the meter, bars, the before/after split, chapter cards, lower
// thirds, sourced quotes, the timeline and the Gemini agent's live trace. Entrances are CSS (they pause with the
// show: .shw.is-paused stops every animation); anything that happens at a moment in the scene (a counter's leap, an
// agent step, a timeline tick) follows the engine's scene clock through useTicks, so pausing freezes it too.

const sig = (l) => JSON.stringify(l)

export default function SceneOverlays({ engine, scene, prevScene, est, leaving, words }) {
  const layers = scene?.layers || []
  const before = useMemo(() => new Set((prevScene?.layers || []).map(sig)), [prevScene])
  const prevCounter = (prevScene?.layers || []).find((l) => l.type === 'counter')
  const of = (type) => layers.filter((l) => l.type === type)
  const held = (l) => !!leaving || before.has(sig(l)) // leaving: shown as it ended, while it fades
  const common = { engine, scene, est }
  const docked = mapBusy(scene)
  let right = layers.filter((l) => ['counter', 'meter', 'bars', 'quote'].includes(l.type) || (docked && l.type === 'compare'))
  const left = of('agent')
  const lower = of('lower_third')
  const tl = of('timeline')
  const title = of('title')
  const compare = docked ? [] : of('compare')
  // a sourced fact with nothing else on screen is the scene: set large, like a documentary's pull quote
  const feature = !docked && !title.length && !left.length && !compare.length && right.length && right.every((l) => l.type === 'quote') ? right : []
  if (feature.length) right = []
  return (
    <div
      className={`shw-ov${leaving ? ' is-leaving' : ''}${title.length || feature.length ? ' has-title' : ''}${right.length ? ' has-right' : ''}`}
      aria-hidden={leaving || undefined}
    >
      {feature.length > 0 && (
        <div className="shw-feature">
          {feature.map((l, i) => (
            <Quote key={`f${i}`} layer={l} held={held(l)} words={words} />
          ))}
        </div>
      )}
      {title.map((l, i) => (
        <TitleCard key={`t${i}`} layer={l} held={held(l)} chapter={scene?.chapter} />
      ))}
      {compare.map((l, i) => (
        <Compare key={`c${i}`} layer={l} held={held(l)} />
      ))}
      {left.length > 0 && (
        <div className="shw-col shw-col--left">
          {left.map((l, i) => (
            <Agent key={`a${i}`} layer={l} held={held(l)} {...common} />
          ))}
        </div>
      )}
      {right.length > 0 && (
        <div className="shw-col shw-col--right">
          {right.map((l, i) => {
            const h = held(l)
            if (l.type === 'counter')
              return <Counter key={`n${i}`} layer={l} held={h} carried={!h && prevCounter?.label === l.label} {...common} />
            if (l.type === 'meter') return <Meter key={`m${i}`} layer={l} held={h} words={words} />
            if (l.type === 'bars') return <Bars key={`b${i}`} layer={l} held={h} />
            if (l.type === 'compare') return <Compare key={`c${i}`} layer={l} held={h} card />
            return <Quote key={`q${i}`} layer={l} held={h} words={words} />
          })}
        </div>
      )}
      {(lower.length > 0 || tl.length > 0) && (
        <div className="shw-band">
          {tl.map((l, i) => (
            <Timeline key={`l${i}`} layer={l} held={held(l)} {...common} />
          ))}
          {lower.map((l, i) => (
            <LowerThird key={`w${i}`} layer={l} held={held(l)} />
          ))}
        </div>
      )}
    </div>
  )
}

const cls = (base, held) => `${base}${held ? ' is-held' : ''}`

// ------------------------------------------------------------------ the counter: it only leaps
// With a blackout spreading in the same scene, it leaps as each area goes dark (by the people there); otherwise
// once. Every leap is one short hard jolt, bigger as the number grows; a loss counter reddens as it climbs.
function leapPlan(layer, scene, est) {
  const from = Number(layer.from) || 0
  const to = Number(layer.to) || 0
  // the engine's own leaps (a cascade's people hit step by step, lines down as the storm reaches them), each at its
  // moment in the scene: the same clock the map's lines and zones play on
  if (Array.isArray(layer.leaps) && layer.leaps.length) {
    const out = layer.leaps.map((x) => ({ at: atMs(layer, Number(x.at) || 0, est), value: Number(x.value) || 0 }))
    out.sort((a, b) => a.at - b.at)
    if (out[out.length - 1].value !== to) out.push({ at: out[out.length - 1].at + 400, value: to })
    return out
  }
  const zones = (scene?.layers || []).find((l) => l.type === 'zones' && l.style === 'blackout' && l.animate === 'spread' && (l.items || []).length > 1)
  if (zones && layer.tone === 'loss') {
    const items = zones.items.slice(0, 40)
    const stagger = zones.stagger_ms ?? 550
    const w = items.map((z) => Math.max(0, Number(z.weight ?? z.people) || 0))
    const total = w.reduce((a, b) => a + b, 0)
    const n = items.length
    // at most eight leaps: the zones in groups
    const groups = Math.min(8, n)
    const out = []
    let acc = 0
    for (let gi = 0; gi < groups; gi++) {
      const lo = Math.floor((gi * n) / groups)
      const hi = Math.floor(((gi + 1) * n) / groups)
      for (let k = lo; k < hi; k++) acc += total > 0 ? w[k] / total : 1 / n
      out.push({ at: 250 + (hi - 1) * stagger + 650, value: gi === groups - 1 ? to : from + (to - from) * Math.min(1, acc) })
    }
    return out
  }
  return [{ at: 900, value: to }]
}

function Counter({ engine, scene, layer, held, carried, est }) {
  const plan = useMemo(() => leapPlan(layer, scene, est), [layer, scene, est])
  const times = useMemo(() => plan.map((p) => p.at), [plan])
  const n = useTicks(engine, times, held)
  const from = Number(layer.from) || 0
  const to = Number(layer.to) || 0
  const value = n === 0 ? from : plan[n - 1].value
  const prev = n <= 1 ? from : plan[n - 2].value
  const span = Math.max(1, Math.abs(to - from))
  const k = Math.min(1, Math.abs(value - from) / span)
  const amp = n === 0 || held ? 0 : 2 + 9 * Math.min(1, Math.abs(value - prev) / span) + 3 * k
  const p = parts(value, layer.format)
  const tone = layer.tone || 'neutral'
  return (
    <section className={cls(`shw-counter shw-counter--${tone}${carried ? ' is-carried' : ''}`, held)} style={{ '--k': k.toFixed(3) }}>
      <p className="shw-counter__label">{layer.label}</p>
      <p className="shw-counter__num" key={n} data-leap={n} style={{ '--amp': `${amp.toFixed(1)}px` }}>
        {p.pre && <span className="shw-counter__pre">{p.pre}</span>}
        {p.num}
        {p.unit && <span className="shw-counter__unit"> {p.unit}</span>}
      </p>
      <span className="shw-sr">{`${layer.label}: ${fmtValue(to, layer.format)}`}</span>
    </section>
  )
}

// ------------------------------------------------------------------ the meter: cells fill one by one
function Meter({ layer, held, words }) {
  const cells = Math.max(1, Math.min(40, Math.round(layer.cells || 10)))
  const today = Math.max(0, Math.min(cells, Math.round(layer.today || 0)))
  const filled = Math.max(today, Math.min(cells, Math.round(layer.filled ?? today)))
  const reserve = layer.reserve_at != null ? Math.max(0, Math.min(cells, Math.round(layer.reserve_at))) : null
  const [shown, setShown] = useState(held ? filled : today)
  // the count beside the meter follows the cells as they fill (CSS delays: 700 ms + 260 ms a cell)
  const ref = useRef(null)
  useEffect(() => {
    if (held) return undefined
    const el = ref.current
    if (!el) return undefined
    const on = (e) => {
      const i = Number(e.target?.dataset?.cell)
      if (i) setShown((s) => Math.max(s, i)) // a cell finished filling (the fill, or its fade with reduced motion)
    }
    el.addEventListener('animationend', on)
    return () => el.removeEventListener('animationend', on)
  }, [held])
  return (
    <section className={cls('shw-meter', held)}>
      <p className="shw-meter__label">{layer.label}</p>
      <p className="shw-meter__count">
        <span className="shw-meter__big">{shown}</span>
        <span className="shw-meter__today">
          {words?.today || 'today'} {today}
        </span>
      </p>
      <div className="shw-meter__cells" ref={ref} style={{ '--cells': cells }} role="img" aria-label={`${today} today, ${filled} with the plan, of ${cells}`}>
        {Array.from({ length: cells }, (_, i) => {
          const c = i + 1
          const state = c <= today ? 'today' : c <= filled ? 'new' : 'empty'
          const past = reserve != null && c > reserve
          return (
            <span
              key={i}
              data-cell={c}
              className={`shw-cell shw-cell--${state}${past ? ' is-past' : ''}`}
              style={state === 'new' ? { animationDelay: `${700 + (c - today - 1) * 260}ms` } : undefined}
            />
          )
        })}
        {reserve != null && reserve < cells && <span className="shw-meter__reserve" style={{ '--at': reserve }} />}
      </div>
      {reserve != null && reserve < cells && (
        <p className="shw-meter__note">
          <span className="shw-meter__swatch" aria-hidden="true" /> {words?.reserveNote || "Past the power plants' reserve line: also needs new plants or flexible campuses"}
        </p>
      )}
    </section>
  )
}

// ------------------------------------------------------------------ bars build
function Bars({ layer, held }) {
  const items = (layer.items || []).slice(0, 8)
  const max = Math.max(1, ...items.map((b) => Math.abs(Number(b.value) || 0)))
  return (
    <section className={cls('shw-bars', held)}>
      {layer.title && <p className="shw-bars__title">{layer.title}</p>}
      <ul className="shw-bars__list">
        {items.map((b, i) => (
          <li key={i} className={`shw-bar shw-bar--${b.tone || 'neutral'}`} style={{ '--w': (Math.abs(Number(b.value) || 0) / max).toFixed(4), animationDelay: `${250 + i * 140}ms` }}>
            <span className="shw-bar__label">{b.label}</span>
            <span className="shw-bar__track">
              <span className="shw-bar__fill" style={{ animationDelay: `${350 + i * 140}ms` }} />
            </span>
            <span className="shw-bar__value" style={{ animationDelay: `${900 + i * 140}ms` }}>
              {fmtValue(b.value, layer.format)}
            </span>
          </li>
        ))}
      </ul>
    </section>
  )
}

// ------------------------------------------------------------------ before / after
function Compare({ layer, held, card }) {
  const L = layer.left || {}
  const R = layer.right || {}
  return (
    <section className={cls(`shw-compare shw-compare--${layer.tone || 'neutral'}${card ? ' shw-compare--card' : ''}`, held)}>
      <div className="shw-compare__side shw-compare__side--left">
        <p className="shw-compare__label">{L.label}</p>
        <CompareValue value={L.value} format={L.format} />
        {L.sub && <p className="shw-compare__sub">{L.sub}</p>}
      </div>
      <span className="shw-compare__rule" aria-hidden="true" />
      <div className="shw-compare__side shw-compare__side--right">
        <p className="shw-compare__label">{R.label}</p>
        <CompareValue value={R.value} format={R.format} />
        {R.sub && <p className="shw-compare__sub">{R.sub}</p>}
      </div>
    </section>
  )
}

// the figure large, its unit (a number's own, or the words after it: "2,010 megawatts") small beside it
function CompareValue({ value, format }) {
  let num = value
  let unit = ''
  let pre = ''
  if (typeof value === 'number') ({ pre, num, unit } = parts(value, format))
  else {
    const m = String(value ?? '').match(/^([$€]?[\d.,]+\s?%?)\s+(\S.*)$/)
    if (m) [num, unit] = [m[1], m[2]]
  }
  return (
    <p className="shw-compare__value">
      {pre}
      {num}
      {unit && <span className="shw-compare__unit"> {unit}</span>}
    </p>
  )
}

// ------------------------------------------------------------------ a chapter card
function TitleCard({ layer, held, chapter }) {
  return (
    <section className={cls('shw-title', held)}>
      {chapter && chapter !== layer.text && <p className="shw-title__chapter">{chapter}</p>}
      <h2 className="shw-title__text">{layer.text}</h2>
      {layer.sub && <p className="shw-title__sub">{layer.sub}</p>}
    </section>
  )
}

function LowerThird({ layer, held }) {
  const legend = Array.isArray(layer.legend) ? layer.legend.filter((x) => x?.text) : []
  return (
    <section className={cls('shw-lower', held)}>
      <p className="shw-lower__text">{layer.text}</p>
      {layer.sub && <p className="shw-lower__sub">{layer.sub}</p>}
      {legend.length > 0 && (
        <ul className="shw-legend" aria-label="Map key">
          {legend.map((x, i) => (
            <li key={i} className={`shw-legend__item shw-legend__item--${x.swatch === 'b' ? 'b' : 'a'}`}>
              <span className="shw-legend__swatch" aria-hidden="true" />
              {x.text}
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}

function Quote({ layer, held, words }) {
  return (
    <figure className={cls(`shw-quote${String(layer.text || '').length > 260 ? ' is-long' : ''}`, held)}>
      <blockquote className="shw-quote__text">{layer.text}</blockquote>
      {layer.source && (
        <figcaption className="shw-quote__source">
          {words?.source || 'Source'}: {layer.source}
        </figcaption>
      )}
    </figure>
  )
}

// ------------------------------------------------------------------ the timeline: a playhead passes each moment
// At most seven ticks: a longer run (a cascade's twenty steps) shows seven of them evenly, the first and the last
// included; spanned items tick at their own moment (the step the map is showing), others spread over the whole scene.
function Timeline({ engine, layer, held, est }) {
  const items = useMemo(() => {
    const all = layer.items || []
    if (all.length <= 7) return all
    return Array.from({ length: 7 }, (_, k) => all[Math.round((k * (all.length - 1)) / 6)])
  }, [layer])
  const times = useMemo(() => {
    if (spanned(layer) && items.every((it) => it.at != null)) return items.map((it) => atMs(layer, it.at, est))
    const span = Math.max(2400, est - 3200)
    return items.map((_, i) => 600 + (items.length > 1 ? (i / (items.length - 1)) * span : 0))
  }, [items, est, layer])
  const n = useTicks(engine, times, held)
  const head = items.length > 1 ? Math.max(0, n - 1) / (items.length - 1) : 1
  return (
    <section className={cls('shw-timeline', held)} style={{ '--n': items.length }}>
      <span className="shw-timeline__rail" aria-hidden="true">
        <span className="shw-timeline__head" style={{ transform: `scaleX(${n ? head : 0})` }} />
      </span>
      <ol className="shw-timeline__items">
        {items.map((it, i) => (
          <li key={i} className={`shw-tick shw-tick--${it.tone || 'neutral'}${i < n ? ' is-on' : ''}`}>
            <span className="shw-tick__dot" aria-hidden="true" />
            {it.t != null && !String(it.label || '').includes(String(it.t)) && <span className="shw-tick__t">{it.t}</span>}
            <span className="shw-tick__label">{it.label}</span>
          </li>
        ))}
      </ol>
    </section>
  )
}

// ------------------------------------------------------------------ a Gemini agent at work
const KIND = {
  tool: 'calls a tool',
  propose: 'proposes',
  verify: 'checks',
  check: 'checks',
  reject: 'rejects',
  accept: 'accepts',
  draft: 'drafts',
  rewrite: 'rewrites',
}

function Agent({ engine, layer, held, est }) {
  const steps = useMemo(() => (layer.steps || []).slice(0, 12), [layer])
  const times = useMemo(() => {
    const gap = Math.max(650, Math.min(1900, (est - 1600) / Math.max(1, steps.length)))
    return steps.map((_, i) => 700 + i * gap)
  }, [steps, est])
  const n = useTicks(engine, times, held)
  const nextActor = steps[n]?.actor
  const gemini = steps.some((s) => s.actor === 'gemini')
  const listRef = useRef(null)
  useEffect(() => {
    const el = listRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [n])
  // what a rejection turns down is struck through: a Gemini step that failed on its own (its own tool call came back
  // no) is itself the rejection; the engine's verdict strikes the proposal it answers, the nearest one before it
  const struck = new Set()
  steps.slice(0, n).forEach((s, i) => {
    if (s.kind !== 'reject') return
    if (s.actor === 'gemini') {
      struck.add(i)
      return
    }
    for (let j = i - 1; j >= 0; j--)
      if (steps[j].kind === 'propose') {
        struck.add(j)
        break
      }
  })
  return (
    <section className={cls('shw-agent', held)} aria-label={`Gemini agent: ${layer.title || ''}`}>
      <header className="shw-agent__head">
        <span className={`shw-agent__live${n >= steps.length ? ' is-done' : ''}`} aria-hidden="true" />
        <p className="shw-agent__who">{gemini ? 'Gemini agent' : 'Rule-based agent'}</p>
        <p className="shw-agent__title">{layer.title}</p>
      </header>
      <ol className="shw-agent__steps" ref={listRef}>
        {steps.slice(0, n).map((s, i) => (
          <li key={i} className={`shw-step shw-step--${s.actor === 'engine' ? 'engine' : 'gemini'} shw-step--${s.kind}${struck.has(i) ? ' is-struck' : ''}`}>
            <span className="shw-step__who">
              {actorName(s.actor)} <span className="shw-step__kind">{KIND[s.kind] || s.kind}</span>
            </span>
            <span className="shw-step__text">{s.text}</span>
          </li>
        ))}
        {n < steps.length && (
          <li className="shw-step shw-step--pending" aria-hidden="true">
            <span className="shw-step__who">{actorName(nextActor)}</span>
            <span className="shw-dots">
              <i />
              <i />
              <i />
            </span>
          </li>
        )}
      </ol>
    </section>
  )
}
