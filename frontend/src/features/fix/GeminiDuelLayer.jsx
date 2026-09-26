// FEATURE: "Let Gemini fix it" — the AI proposer's recorded run replayed on the map, in about 12-20 s. Each plan
// Gemini proposed glows in the Gemini blue with what it adds ("+593 MVA"); the engine's verdict follows: the lines
// still over their limit flash red with their loading, or the plan turns green with the engine's price for it; the
// findings go back, the revision comes in; the run ends on the plan the contest ends on and hands off to "Run it
// again with the fix". Only real recorded steps (backend/solutions.py → report.agentic.trace; features/fix/duel.js).
//
//   default export GeminiDuelLayer()  SVG inside the map camera (App.jsx mounts it with the other layers); it also
//                                     portals the side card into the page
//   GeminiDuelOffer({ rate })         the button after the cascade (shell/ImpactPanel.jsx), under the flip
import { useEffect, useMemo, useRef } from 'react'
import { createPortal } from 'react-dom'
import { HEIGHT, WIDTH, fmt, project as projectLonLat } from '../../geo'
import { useMapView } from '../../GridMap'
import { useOverload } from '../../store'
import { Button } from '../../ui'
import AgentTrace from '../ai/AgentTrace'
import AiBadge from '../ai/AiBadge'
import { groupTrace, loc } from '../ai/trace'
import { bodyFor } from '../briefing/stage'
import { money } from '../cost/money'
import { useReducedMotion } from '../impact/towns'
import './duel.css'
import { closeDuel, outcomeOf, pauseDuel, pctText, resumeDuel, skipDuel, startDuel, useDuel } from './duel'
import { fixLine, flipSide, plantsOut, runWithFix, showWith, useBestFix, useFlip } from './flipCase'

const bare = (label) => String(label || '').replace(/^the\s+/i, '')
const cap = (s) => (s ? s[0].toUpperCase() + s.slice(1) : s)
const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`
const added = (l) => Math.max(0, Math.round((Number(l.to_mva) || 0) - (Number(l.from_mva) || 0)))

// ------------------------------------------------------------------ the offer (results column, after the cascade)
export function GeminiDuelOffer({ rate }) {
  const O = useOverload()
  const reduced = useReducedMotion()
  const duel = useDuel()
  const plantCase = plantsOut(O.cascade)
  const body = useMemo(() => (O.cascade && !plantCase ? bodyFor(O.cascade, O.caseBody) : null), [O.cascade, O.caseBody, plantCase])
  // the same poller as the flip's button (flipCase.js: one per case), which asks again until the proposer is done
  const { report, fix, polling, recheck } = useBestFix(body, !!body)
  if (plantCase || !report || !fix) return null
  const ag = report.agentic
  if (!ag || ag.status === 'off') return null
  if (ag.status === 'running')
    return (
      <p className="duel-offer__note" role="status">
        <AiBadge by="gemini">working</AiBadge> Gemini is proposing plans; the engine re-runs each one…
        {!polling && (
          <>
            {' '}
            <button type="button" className="duel-offer__again" onClick={recheck}>
              Check again
            </button>
          </>
        )}
      </p>
    )
  if (ag.status !== 'done' || !(ag.asked > 0))
    return (
      <p className="duel-offer__note">
        <AiBadge by="fallback" why="Gemini did not answer" /> The fix above is the engine&apos;s own.
      </p>
    )
  const on = duel.status !== 'idle' && duel.agentic === ag
  const rounds = ag.rounds || 1
  return (
    <button
      type="button"
      className={`duel-offer${on ? ' duel-offer--on' : ''}`}
      aria-pressed={on}
      onClick={() => startDuel({ agentic: ag, body, report, fix, rate, cascade: O.cascade, reduced })}
    >
      <span className="duel-offer__a">
        <span className="duel-offer__dot" aria-hidden="true" />
        Let Gemini fix it
      </span>
      <span className="duel-offer__q">
        Replay its {plural(ag.asked, 'plan', 'plans')} in {plural(rounds, 'round', 'rounds')} on the map, and the engine&apos;s verdict on each
      </span>
    </button>
  )
}

// ------------------------------------------------------------------ what the map shows at a beat
// [{id, tone: 'gemini' | 'dim' | 'over' | 'hot' | 'holds', label, tag}]: a `tag` is a label only (the engine's
// verdict, by the plan's first line), no line of its own. Red ('over') only for a line past its rating; a line a
// cheaper plan left hot but within its rating is amber ('hot'), with one decimal (99.7 %)
const overLine = (o) => (o.hot || Number(o.pct) < 100 ? { id: o.id, tone: 'hot', label: pctText(o.pct, true) } : { id: o.id, tone: 'over', label: pctText(o.pct) })
function sceneOf(beat) {
  if (!beat) return []
  const planLines = (row, tone) => (row?.lines || []).map((l) => ({ id: l.id, tone, label: `+${fmt(added(l))} MVA` }))
  const tag = (row, tone, label) => (row?.lines?.[0] ? [{ id: row.lines[0].id, tone, label, tag: true }] : [])
  const holdsTag = (plan, v) => tag(plan, 'holds', `holds${v?.cost_usd != null ? ` · ${money(v.cost_usd)}` : ''} · engine-verified`)
  if (beat.kind === 'plan') return planLines(beat.row, 'gemini')
  if (beat.kind === 'verdict') {
    const v = beat.row
    if (v.holds) return [...planLines(beat.plan, 'holds'), ...holdsTag(beat.plan, v)]
    const over = (v.over || []).map(overLine)
    const ids = new Set(over.map((o) => o.id))
    const n = v.over_count ?? over.length
    return [
      ...planLines(beat.plan, 'dim').filter((l) => !ids.has(l.id)).map((l) => ({ ...l, label: null })),
      ...over,
      ...(over[0] ? [{ id: over[0].id, tone: 'over', label: `fails${n ? ` · ${plural(n, 'line', 'lines')} still over` : ''}`, tag: true }] : []),
    ]
  }
  if (beat.kind === 'feedback') return (beat.over || []).map(overLine)
  if (beat.kind === 'result' && beat.best) return [...planLines(beat.best.plan, 'holds'), ...holdsTag(beat.best.plan, beat.best.verdict)]
  return []
}

// every line the run touches: where the camera goes
function allIds(beats) {
  const ids = new Set()
  beats.forEach((b) => {
    ;(b.row?.lines || []).forEach((l) => ids.add(l.id))
    ;(b.row?.over || []).forEach((o) => ids.add(o.id))
  })
  return [...ids]
}

// the labels, nudged down (in screen pixels) until no two overlap: a transformer and the line leaving it sit
// almost on top of each other
const LABEL_H = 16
function placeLabels(items, k) {
  const placed = []
  return items
    .filter((it) => it.label)
    .map((it) => {
      const end = it.a[0] >= it.b[0] ? it.a : it.b
      const [x, y] = it.xf ? [it.a[0] + 13 / k, it.a[1] - 3 / k] : [end[0] + 10 / k, end[1] - 3 / k]
      return { key: `${it.tag ? 'tag' : ''}${it.id}`, tone: it.tone, tag: !!it.tag, text: it.label, sx: x * k, sy: y * k, w: it.label.length * 7.6 }
    })
    // the tags go last, so they settle under the line labels
    .sort((p, q) => p.tag - q.tag || p.sy - q.sy)
    .map((l) => {
      const hits = (p) => l.sx < p.sx + p.w && p.sx < l.sx + l.w && Math.abs(p.sy - l.sy) < LABEL_H
      for (let n = 0; n < 10 && placed.some(hits); n++) l.sy += LABEL_H
      placed.push(l)
      return { ...l, x: l.sx / k, y: l.sy / k }
    })
}

// ------------------------------------------------------------------ the layer
export default function GeminiDuelLayer() {
  const O = useOverload()
  const { branchById, subPos, focus, cascade, mode } = O
  const { k, project } = useMapView()
  const duel = useDuel()
  const on = duel.status !== 'idle'
  const beat = on ? duel.beats[duel.i] : null

  // a new case, or the Strengthen page: the replay belongs to the case it started on
  useEffect(() => {
    if (on && (cascade !== duel.cascade || mode === 'unlock')) closeDuel()
  }, [on, cascade, duel.cascade, mode])

  // the camera frames every line the run touches, once, when it starts
  const framed = useRef(0)
  const zoomTimers = useRef([])
  useEffect(() => {
    if (!on || framed.current === duel.started) return
    framed.current = duel.started
    const pts = allIds(duel.beats).flatMap((id) => {
      const b = branchById.get(Number(id))
      return b ? [subPos(b.from_sub), subPos(b.to_sub)] : []
    })
    if (!pts.length) return
    focus(pts)
    // the focus stops at a wide box (GridMap FOCUS_MIN_BOX): the plans sit on a few lines around one substation, so
    // the camera then steps in with the map's own zoom control, as far as every line still fits between the panels
    const xy = pts.map(([lon, lat]) => projectLonLat(lon, lat))
    const xs = xy.map((q) => q[0])
    const ys = xy.map((q) => q[1])
    const ex = Math.max(...xs) - Math.min(...xs)
    const ey = Math.max(...ys) - Math.min(...ys)
    // GridMap's own focus: the box it fits (at least FOCUS_MIN_BOX), capped at FOCUS_MAX_ZOOM
    const kFocus = Math.min(6, Math.max(1, Math.min(WIDTH / Math.max(ex + 40, 140), HEIGHT / Math.max(ey + 40, 140))))
    // screen pixels per map unit at zoom 1 (the SVG fits its viewBox: "meet"), then the zoom at which the lines fill
    // about 40 % of the map's width and half its height (MAX_ZOOM 12 at most)
    const r = document.querySelector('.mc .map-svg')?.getBoundingClientRect()
    const ppu = r ? Math.min(r.width / WIDTH, r.height / HEIGHT) : 1
    const kFit = r ? Math.min(12, (0.4 * r.width) / ((ex + 4) * ppu), (0.5 * r.height) / ((ey + 4) * ppu)) : kFocus
    let steps = 0
    for (let kk = kFocus; kk * 1.6 <= kFit && steps < 3; kk *= 1.6) steps += 1
    const zoomIn = () => document.querySelector('.mc .map-ctl[aria-label="Zoom in"]')?.click()
    zoomTimers.current.forEach(clearTimeout)
    zoomTimers.current = Array.from({ length: steps }, (_, n) => setTimeout(zoomIn, 950 + n * 80))
    // on a phone the map sits above the results: bring it into view
    if (window.matchMedia?.('(max-width: 860px)').matches) document.querySelector('.mc .map')?.scrollIntoView?.({ block: 'start', behavior: 'smooth' })
  }, [on, duel.started, duel.beats, branchById, subPos, focus])
  // closed (or unmounted) before the camera finished stepping in: the steps left are dropped
  useEffect(() => {
    if (on) return undefined
    zoomTimers.current.forEach(clearTimeout)
    zoomTimers.current = []
    return undefined
  }, [on])
  useEffect(() => () => zoomTimers.current.forEach(clearTimeout), [])

  const drawn = useMemo(
    () =>
      sceneOf(beat)
        .map((l) => {
          const b = branchById.get(Number(l.id))
          const a = b && subPos(b.from_sub)
          const z = b && subPos(b.to_sub)
          if (!a || !z) return null
          return { ...l, a: project(a[0], a[1]), b: project(z[0], z[1]), xf: b.from_sub === b.to_sub }
        })
        .filter(Boolean),
    [beat, branchById, subPos, project],
  )
  const labels = useMemo(() => placeLabels(drawn, k), [drawn, k])
  if (!on) return null
  const key = `${duel.started}-${duel.i}`
  return (
    <g className={`duel-map duel-map--${beat?.kind || 'none'}`} aria-hidden="true">
      {/* the cascade's aftermath steps back while the plans are tested */}
      <rect className="duel-map__dim" x={-20000} y={-20000} width={60000} height={60000} />
      <g key={key}>
        {drawn.filter((l) => !l.tag).map((l) =>
          l.xf ? (
            <circle key={`${l.tone}${l.id}`} className={`duel-ln duel-ln--${l.tone}`} cx={l.a[0]} cy={l.a[1]} r={9 / k} pathLength="1" />
          ) : (
            <line key={`${l.tone}${l.id}`} className={`duel-ln duel-ln--${l.tone}`} x1={l.a[0]} y1={l.a[1]} x2={l.b[0]} y2={l.b[1]} pathLength="1" />
          ),
        )}
        {labels.map((l) => (
          <text key={l.key} className={`duel-lbl duel-lbl--${l.tone}${l.tag ? ' duel-lbl--tag' : ''}`} x={l.x} y={l.y} fontSize={(l.tag ? 12 : 13) / k} strokeWidth={4 / k}>
            {l.text}
          </text>
        ))}
      </g>
      <DuelCard />
    </g>
  )
}

// ------------------------------------------------------------------ the side card
function DuelCard() {
  const duel = useDuel()
  const host = typeof document !== 'undefined' ? document.querySelector('.mc') : null
  const cardRef = useRef(null)
  // Escape closes it, unless it is meant for something else: a modal over the page (the presentation, a site
  // report) owns Escape while it is open, and a field being typed in outside the card only loses focus
  useEffect(() => {
    const onKey = (e) => {
      if (e.key !== 'Escape' || e.defaultPrevented) return
      if (document.querySelector('[aria-modal="true"]')) return
      const t = e.target
      const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(t?.tagName || '') || t?.isContentEditable
      if (typing && !cardRef.current?.contains(t)) return
      closeDuel()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])
  if (!host) return null
  // the card is portaled out of the map's SVG, but React still bubbles its events through the map's handlers: a
  // click on Skip would otherwise land on the map as a click that places the campus there
  const stop = (e) => e.stopPropagation()
  return createPortal(
    <section
      className="duel"
      aria-label="Gemini against the engine"
      ref={cardRef}
      onPointerDown={stop}
      onPointerMove={stop}
      onPointerUp={stop}
      onPointerCancel={stop}
      onClick={stop}
      onDoubleClick={stop}
      onWheel={stop}
      onDragOver={stop}
      onDrop={stop}
      onContextMenu={stop}
    >
      <CardBody duel={duel} />
    </section>,
    host,
  )
}

function CardBody({ duel }) {
  const O = useOverload()
  const flip = useFlip()
  const { beats, i, status, trace } = duel
  const beat = beats[i]
  const done = status === 'done'
  const last = beats[beats.length - 1]
  const res = last?.kind === 'result' ? last.row : null
  // the bar and Gemini's best so far (the verified plans up to this beat), from the recorded prices, picked the way
  // the backend picks the result's plan (solutions.py propose: a win first, then the whole campus, then any): a
  // cheaper plan that leaves less margin never shows as the best once a winning plan is in. At the end: the
  // result's own figure, so the box and the final title always name the same plan
  const seen = beats.slice(0, i + 1).filter((b) => b.kind === 'verdict' && b.row.holds && b.row.cost_usd != null)
  const bar = trace.find((r) => r.engine_cost_usd != null)?.engine_cost_usd ?? null
  const tier = (b) => (b.row.vs === 'beat' ? 0 : (b.row.keep_pct ?? 100) >= 99.5 ? 1 : 2)
  const bestSoFar = seen.reduce((a, b) => (!a || tier(b) < tier(a) || (tier(b) === tier(a) && b.row.cost_usd < a.row.cost_usd) ? b : a), null)
  const bestCost = done && res?.best_cost_usd != null ? res.best_cost_usd : (bestSoFar?.row.cost_usd ?? null)
  const bestWins = done && res?.outcome ? res.outcome === 'beat' : bestSoFar?.row.vs === 'beat'
  // the list under it: the rows already played (the current one is in the box above)
  const items = useMemo(() => groupTrace(trace), [trace])
  const curN = beat?.row?.n ?? 0
  const shown = done ? items.length : items.filter((it) => (it.verdict || it.head).n < curN).length
  const outcome = outcomeOf(duel.agentic)
  const known = flipSide(flip, O.caseBody) === 'base' ? flip.fix : null
  const fix = known || duel.fix
  const handOff = () => {
    closeDuel()
    if (known) showWith(O)
    else runWithFix(O, duel.fix, { base: duel.body, report: duel.report, rate: duel.rate })
  }
  const where = !beat
    ? ''
    : beat.kind === 'plan' || beat.kind === 'verdict'
      ? `Round ${beat.round} of ${beat.rounds} · plan ${beat.planNo} of ${beat.plans}`
      : beat.kind === 'feedback'
        ? `Round ${beat.round} of ${beat.rounds} · the findings go back`
        : beat.kind === 'result'
          ? `${plural(beat.rounds, 'round', 'rounds')} · ${plural(duel.agentic?.asked || 0, 'plan', 'plans')} tested by the engine`
          : `Round ${beat.round} of ${beat.rounds}`

  return (
    <>
      <header className="duel__head">
        <div className="duel__title">
          <h2 className="duel__h">Gemini against the engine</h2>
          <p className="duel__where">{where}</p>
        </div>
        <div className="duel__ctl">
          {!done && (
            <button type="button" className="duel__btn" onClick={status === 'paused' ? resumeDuel : pauseDuel}>
              {status === 'paused' ? 'Play' : 'Pause'}
            </button>
          )}
          {!done && (
            <button type="button" className="duel__btn" onClick={skipDuel}>
              Skip
            </button>
          )}
          <button type="button" className="duel__btn duel__btn--x" onClick={closeDuel} aria-label="Close Gemini against the engine">
            ×
          </button>
        </div>
      </header>

      <dl className="duel__score" aria-label="The bar and Gemini's best so far">
        <div className="duel__side">
          <dt>Engine&apos;s own plan</dt>
          <dd>{bar != null ? money(bar) : '—'}</dd>
        </div>
        <div className={`duel__side duel__side--gemini${bestWins ? ' duel__side--win' : ''}`}>
          <dt>{done ? 'Gemini’s best' : 'Gemini’s best so far'}</dt>
          <dd>{bestCost != null ? money(bestCost) : '—'}</dd>
        </div>
      </dl>

      <div className="duel__now" aria-live="polite" key={`${duel.started}-${i}`}>
        {done ? <Final res={res} outcome={outcome} best={last?.best} /> : <Now beat={beat} />}
      </div>

      {done && fix && (
        <div className="duel__hand">
          <Button onClick={handOff}>Run it again with the fix</Button>
          <p className="duel__fixline">{fixLine(fix)}</p>
        </div>
      )}

      <AgentTrace trace={trace} shown={shown} heading={false} brief follow className="duel__trace" stepMs={500} />
      <p className="duel__note">
        A replay of the recorded run: Gemini proposed, the engine re-ran every plan through the full cascade and priced it (published per-mile and
        per-MVA figures, high end). Synthetic grid model; estimates.
      </p>
    </>
  )
}

function Who({ by }) {
  return <span className={`duel__who duel__who--${by}`}>{by === 'gemini' ? 'Gemini' : 'Engine'}</span>
}

function PlanLines({ row }) {
  const lines = row?.lines || []
  const more = Math.max(0, (row?.lines_total || lines.length) - lines.length)
  return (
    <ul className="duel__lines">
      {lines.map((l) => (
        <li key={l.id}>
          <b>+{fmt(added(l))} MVA</b> {cap(bare(l.label))} <span className="duel__mva">({fmt(l.from_mva)} → {fmt(l.to_mva)})</span>
        </li>
      ))}
      {more > 0 && <li className="duel__more">…and {plural(more, 'more line', 'more lines')}</li>}
    </ul>
  )
}

function vsLine(v) {
  const bar = v.engine_cost_usd
  if (bar == null || v.cost_usd == null) return null
  const d = Math.abs(bar - v.cost_usd)
  if (v.vs === 'beat') return `${money(d)} under the engine's own plan`
  if (v.vs === 'match') return 'The same as the engine’s own plan'
  if (v.vs === 'pricier') return `${money(d)} more than the engine's own plan`
  if (v.vs === 'smaller') return `Cheaper, but for ${Math.round(v.keep_pct)} % of the campus: not a win`
  if (v.vs === 'thin') return `Cheaper, but it leaves a line at ${pctText(v.peak_pct)} where the engine's plan leaves none above ${pctText(v.margin_pct)}: not a win`
  return null
}

function Now({ beat }) {
  if (!beat) return null
  const r = beat.row
  if (beat.kind === 'plan')
    return (
      <div className="duel__step duel__step--gemini">
        <Who by="gemini" />
        <p className="duel__say">{loc(r.title)}</p>
        <PlanLines row={r} />
      </div>
    )
  if (beat.kind === 'verdict') {
    const p = beat.plan
    const over = r.over || []
    const n = r.over_count ?? over.length
    return (
      <>
        <div className="duel__step duel__step--gemini duel__step--past">
          <Who by="gemini" />
          <p className="duel__say">{loc(p?.title)}</p>
        </div>
        <div className={`duel__step duel__step--${r.holds ? 'holds' : 'over'}`}>
          <Who by="engine" />
          {r.holds ? (
            <>
              <p className="duel__say">
                Holds: no line trips{r.cost_usd != null && <> · {money(r.cost_usd)}</>} · engine-verified
              </p>
              {vsLine(r) && <p className={`duel__vs duel__vs--${r.vs}`}>{vsLine(r)}</p>}
            </>
          ) : (
            <>
              <p className="duel__say">
                Fails{n > 0 ? `: ${plural(n, 'line', 'lines')} still over` : ''}
              </p>
              <ul className="duel__lines">
                {over.slice(0, 3).map((o) => (
                  <li key={o.id}>
                    <b className="duel__pct">{pctText(o.pct)}</b> {cap(bare(o.label))} <span className="duel__mva">of {fmt(o.mva)} MVA</span>
                  </li>
                ))}
              </ul>
              {r.people > 0 && <p className="duel__sub">The cascade still runs: about {fmt(r.people)} people without power (estimate).</p>}
            </>
          )}
        </div>
      </>
    )
  }
  // the ask, the findings sent back, a skipped plan, Gemini not answering: the recorded row as it is
  return (
    <div className={`duel__step duel__step--${r.actor === 'gemini' ? 'gemini' : 'engine'}`}>
      <Who by={r.actor === 'gemini' ? 'gemini' : 'engine'} />
      <p className="duel__say">{loc(r.title)}</p>
      {loc(r.detail) && <p className="duel__sub">{loc(r.detail)}</p>}
    </div>
  )
}

function Final({ res, outcome, best }) {
  const v = best?.verdict
  const win = outcome === 'beat'
  return (
    <div className={`duel__step duel__final duel__final--${outcome}`}>
      <Who by="engine" />
      <p className="duel__say">{res ? loc(res.title) : 'The run ended.'}</p>
      {best && (
        <p className="duel__sub">
          {win ? 'The plan' : 'Its best'}: {loc(best.plan.title).replace(/^(Proposed|Revised): /, '')}
          {v?.cost_usd != null && <> · {money(v.cost_usd)}</>} <AiBadge by="gemini" verified />
        </p>
      )}
      {res && loc(res.detail) && <p className="duel__sub">{loc(res.detail)}</p>}
    </div>
  )
}
