import { useEffect, useMemo, useRef, useState } from 'react'
import { fmt } from '../../geo'
import { useOverload } from '../../store'
import { chainLayer } from './beatMaps'
import { SETTLE_MS, arcOf, finalHit, finalSnapshot, runsOf, snapshotAt, startSnapshot, stepsIn, syntheticTimeline, timelineOf } from './chainModel'
import { Kicker } from './ShowBits'
import { S } from './showText'
import './chain.css'

// THE CHAIN IN FOUR PARTS (user, Sat 23:37-23:48): the whole cascade as one arc, in order, in view together: where it
// begins and what it extends into, where the chains cause damage, the people the damage hits on its way outward, where
// it finally reaches and the result. Each part is ONE flowing sentence (backend/chain_arc.py, English and Spanish,
// template or Gemini's), never labeled or numbered, with its names, places and numbers set in heavier weight. The parts
// fill in continuously against the map's replay clock (a thin rail runs down them; a sentence writes itself in as its
// part lands; the one being played is lit and the others settle), the counter climbs smoothly through all four, and the
// map lights the lit part's failed elements and the towns it hit. A deck without an arc (an older server) shows the
// slide's own lines.
export default function ShowChain(props) {
  const arc = arcOf(props.slide)
  return arc ? <ChainArc {...props} arc={arc} /> : <ChainPlain {...props} />
}

function ChainPlain({ slide, lang }) {
  const t = S[lang]
  const headline = slide.headline?.[lang] || slide.headline?.en || ''
  const lines = slide.lines?.[lang] || slide.lines?.en || []
  return (
    <>
      <Kicker tone="red">{t.spreadKicker}</Kicker>
      <h2 className="rs-headline" id={`rs-h-${slide.id}`}>
        {headline}
      </h2>
      <ul className="rs-lines">
        {lines.map((x) => (
          <li key={x}>
            <span>{x}</span>
          </li>
        ))}
      </ul>
    </>
  )
}

function ChainArc({ slide, arc, lang, animate, stage, cueStep }) {
  const t = S[lang]
  const o = useOverload()
  const final = finalHit(slide, arc)
  const total = stepsIn(arc)
  const fxRef = useRef(null)
  const cueRef = useRef(0)
  useEffect(() => {
    fxRef.current = o.fx
    cueRef.current = cueStep || 0
  })
  const oRef = useRef(o)
  useEffect(() => {
    oRef.current = o
  })
  const snap = useChainClock(arc, animate, fxRef, cueRef, final)
  const headline = slide.headline?.[lang] || slide.headline?.en || ''

  // the map lights the part being played: its failed elements and the towns it hit (nothing once the replay is over)
  const active = animate ? snap.active : -1
  useEffect(() => {
    if (!stage) return
    if (active < 0) stage.layer(null)
    else stage.layer(chainLayer(oRef.current, arc[active], `chain-${active}`))
  }, [active, arc, stage])
  useEffect(() => () => stage?.layer(null), [stage])
  // the caption: the sentence being played, when the narration isn't speaking it
  useEffect(() => {
    if (!animate || !stage || active < 0) return
    stage.say({ key: `chain-${active}`, text: arc[active].text?.[lang] || '' })
  }, [active, animate, arc, lang, stage])
  useEffect(
    () => () => {
      stage?.say(null)
      stage?.callout(null)
    },
    [stage],
  )

  return (
    <>
      <div className="sh-headrow">
        <Kicker tone="red">{t.spreadKicker}</Kicker>
        {total > 0 && (
          <span className="sh-count" aria-hidden="true">
            {t.step} {Math.min(total, snap.step)} {t.of} {total}
          </span>
        )}
      </div>
      <h2 className="rs-headline sh-headline--small" id={`rs-h-${slide.id}`}>
        {headline}
      </h2>

      <div className="ch-total">
        <span className="ch-total__label">{t.scPeople}</span>
        <b className="ch-total__n" aria-hidden="true">
          {fmt(Math.round(snap.shown))}
        </b>
        <span className="sh-sr">{fmt(final)}</span>
      </div>

      <ol className={`ch-parts${active < 0 && snap.rows.every((r) => r.landed) ? ' ch-parts--settled' : ''}`} aria-label={t.spreadKicker}>
        {arc.map((p, i) => {
          const row = snap.rows[i]
          const cls = row.landed ? (i === active ? ' is-active' : ' is-landed') : ' is-pending'
          return (
            <li key={p.k} className={`ch-part${cls}`} style={{ '--f': row.f.toFixed(4), '--r': row.r.toFixed(4) }}>
              <span className="ch-rail" aria-hidden="true">
                <i />
              </span>
              <p className="ch-part__text">
                {runsOf(p.text?.[lang] || p.text?.en || '', p.marks?.[lang] || p.marks?.en).map((x, j) => (x.b ? <b key={j}>{x.t}</b> : <span key={j}>{x.t}</span>))}
              </p>
              <span className="ch-part__skel" aria-hidden="true">
                <i />
                <i />
              </span>
            </li>
          )
        })}
      </ol>
    </>
  )
}

// The slide's clock. While the blast plays on the map it reads the map's own replay schedule; without one (the map
// isn't playing, or reduced motion shows the finished picture) it walks the parts on a steady clock. The counter
// chases its target with a time constant, so it climbs in many small steps through the whole run, never in a jump.
function useChainClock(arc, animate, fxRef, cueRef, final) {
  const still = useMemo(() => finalSnapshot(arc, final), [arc, final])
  const [snap, setSnap] = useState(() => startSnapshot(arc))
  useEffect(() => {
    if (!animate) return undefined
    const t0 = performance.now()
    let raf = 0
    let last = t0
    let shown = 0
    let tl = null
    let tlFx = null
    let fxSeen = false
    let synthetic = null
    let lastKey = ''
    const tick = (now) => {
      const fx = fxRef.current
      if (fx?.schedule?.tiers?.length) {
        fxSeen = true
        if (fx !== tlFx) {
          tlFx = fx
          tl = timelineOf(fx, arc)
        }
      }
      let el
      if (fxSeen && tl) el = fx ? now - fx.startedAt : Infinity // the replay has ended (or was paused): the finished picture
      else {
        synthetic = synthetic || syntheticTimeline(arc)
        tl = synthetic
        el = now - t0
      }
      const s = snapshotAt(tl, arc, el, { cueStep: cueRef.current, final })
      const dt = Math.min(120, now - last)
      last = now
      shown += (s.hit - shown) * (1 - Math.exp(-dt / SETTLE_MS))
      if (Math.abs(s.hit - shown) < 0.6) shown = s.hit
      // render only when something on screen changed (the finished picture, with a voice still reading, costs nothing)
      const key = `${s.active}|${s.step}|${Math.round(shown)}|${s.rows.map((r) => `${Math.round(r.f * 500)}.${Math.round(r.r * 100)}`).join(',')}`
      if (key !== lastKey) {
        lastKey = key
        setSnap({ ...s, shown })
      }
      if (!(s.done && shown === s.hit && !cueRef.current)) raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [animate, arc, final, fxRef, cueRef, still])
  return animate ? snap : still
}
