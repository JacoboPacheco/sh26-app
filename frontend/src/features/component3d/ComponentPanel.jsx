import { useEffect, useLayoutEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react'
import { useReducedMotion } from '../impact/towns'
import { useOverload } from '../../store'
import { drawScene, fitCanvas, readPalette, readout } from './render3d'
import { END_HOLD, REPORT_URL, elementsFor, kindLine, precedent, shotAt, shotsFor, whyLine } from './shots'
import './component3d.css'

// The failing element, in 3D: a picture-in-picture panel while the cascade replay plays. It sits
// over the lower part of the left column (the scenario controls wait while the replay plays), so
// the map, where the blast and its town labels spread, stays clear; on phones it docks in the page
// flow above the timeline. Each time a step takes a line or transformer out, the panel shows that
// element on the same clock as the map (the trip lands at the tier's t0, the moment the map's line
// snaps): the approach (loading counting up, the conductor heating and sagging) runs before it,
// then the trip, a hold, the next. Drawn by render3d.js on a canvas with no React render per frame;
// the loop runs only while the replay plays and the panel is open and uncovered.
// Stopped (a pause, a jump on the timeline, the end): what the map shows at the store's step (the
// latest failure up to it, tripped; on a Pause mid-approach, the next one still standing), drawn as
// the loop's last frame when that matches (it looks frozen), else as a still. A jump to step 0: nothing.
// After the end: END_HOLD ms, then the panel leaves.
// Reduced motion: a still of each element, over its rating when its approach starts and tripped
// at its trip. Under the presentation (Present the damage) and the show it steps back.
// × hides it for this browser session.

const HIDE_KEY = 'overload.component3d.hidden'
const POST_U = 1600 // a still after the trip: the breaker open, the conductor dark
const PRE_U = -600 // a still just before the trip (reduced motion): at its loading, sagged, the tree upright

function readHidden() {
  try {
    return window.sessionStorage.getItem(HIDE_KEY) === '1'
  } catch {
    return false
  }
}
function writeHidden() {
  try {
    window.sessionStorage.setItem(HIDE_KEY, '1')
  } catch {
    // storage blocked: hidden until the page reloads
  }
}

// the presentation (briefing ReviewStage: html.review-open) and the show (show/ShowHost: html.shw-open)
// cover the page; the panel steps back under them and its loop stops
const COVER_CLASSES = ['review-open', 'shw-open']
function subscribeCover(cb) {
  if (typeof MutationObserver === 'undefined') return () => {}
  const mo = new MutationObserver(cb)
  mo.observe(document.documentElement, { attributes: true, attributeFilter: ['class'] })
  return () => mo.disconnect()
}
const coveredNow = () => COVER_CLASSES.some((c) => document.documentElement.classList.contains(c))
const coveredServer = () => false
function useCovered() {
  return useSyncExternalStore(subscribeCover, coveredNow, coveredServer)
}

/** Mounted in the demo page (App.jsx MissionControl): reads the store. */
export default function ComponentPanel() {
  const o = useOverload()
  const covered = useCovered()
  const { subById, branchById, upgrades, result, branchIndex } = o || {}
  const lookups = useMemo(() => ({ subById, branchById, upgrades, result, branchIndex }), [subById, branchById, upgrades, result, branchIndex])
  if (!o || o.mode === 'unlock') return null
  return <ComponentPanelView cascade={o.cascade} fx={o.fx} playing={o.playing} step={o.step} lookups={lookups} scope={o.mode} covered={covered} />
}

/**
 * The panel for any replay: `cascade`, `fx` ({schedule, startedAt, from} while playing), `playing`,
 * `step` (the store's), `lookups` ({subById, branchById, upgrades, result, branchIndex}).
 * `dock` = 'float' (over the page, placed against its panels) or 'inline' (in a preview).
 * `scope` = the page's mode: a stopped view is dropped when the mode changes (the left column shows
 * another tool). `covered` = something covers the page: the loop stops (the review fades the panel).
 */
export function ComponentPanelView({ cascade, fx, playing, step, lookups, dock = 'float', scope = null, covered = false }) {
  const reduced = useReducedMotion()
  const [hidden, setHidden] = useState(readHidden)
  const elements = useMemo(() => elementsFor(cascade, lookups), [cascade, lookups])
  const shots = useMemo(() => (fx ? shotsFor(fx.schedule, elements) : null), [fx, elements])
  const n = cascade?.steps?.length || 0
  const live = !!(fx && playing && shots?.length)

  // the element the loop shows ({el, cascade}), and where the replay stopped ({step, cascade, scope,
  // paused: the step is the one it played at, so Pause; a jump on the timeline or the end moves it})
  const [shown, setShown] = useState(null)
  const [stop, setStop] = useState(null)
  const [wasLive, setWasLive] = useState(false)
  const [liveStep, setLiveStep] = useState(null)
  if (live && liveStep !== step) setLiveStep(step)
  if (live !== wasLive) {
    setWasLive(live)
    if (!live) setStop({ step, cascade, scope, paused: step === liveStep, covered })
  }
  const ended = !live && !!stop && stop.cascade === cascade && n > 0 && stop.step >= n && step >= n
  const [gone, setGone] = useState(null)
  useEffect(() => {
    if (!ended) return undefined
    const t = setTimeout(() => setGone(stop), END_HOLD)
    return () => clearTimeout(t)
  }, [ended, stop])

  // what is on screen: playing, the live shot. Stopped, what the map shows at the store's step (paused
  // or scrubbed, the map shows that exact step: every failure up to it, the next one still standing):
  // the latest failure up to the step, after its trip; on a Pause while the next one was on screen (its
  // approach, or its trip before the step caught up), that next one standing, over its rating. Until the
  // end's hold runs out (none when the replay ended under the presentation) or the mode changes (the
  // left column then shows another tool).
  let view = null
  if (live) view = { el: shots.some((s) => s.el === shown?.el) ? shown.el : shots[0].el, live: true }
  else if (cascade && !(ended && (gone === stop || stop.covered)) && (!stop || stop.scope === scope)) {
    let cur = null
    for (let j = Math.min(step, n) - 1; j >= 0 && !cur; j--) cur = elements[j]
    let next = null
    for (let j = Math.max(0, step); j < n && !next; j++) next = elements[j]
    const paused = !!stop && stop.paused && stop.cascade === cascade && stop.step === step
    if (paused && next && shown?.cascade === cascade && shown.el.key === next.key) view = { el: next, pre: true }
    else if (cur) view = { el: cur }
    else if (paused && next) view = { el: next, pre: true } // paused before the first failure lands on the step
  }
  const visible = !hidden && !!view
  const stopEl = view && !view.live ? view.el : null
  const stopPre = !!view?.pre

  const canvasRef = useRef(null)
  const figRef = useRef(null)
  const tagRef = useRef(null)
  // the loop's last frame ({key, u, A, tSec}): a stop on the same element, already tripped, redraws it
  // exactly (it looks frozen) instead of jumping to the still
  const frameRef = useRef(null)

  // the live loop: one frame per display refresh while the replay plays
  useEffect(() => {
    if (!live || hidden || covered) return undefined
    const cv = canvasRef.current
    if (!cv) return undefined
    const ctx = cv.getContext('2d')
    const pal = readPalette()
    let raf = 0
    let shownI = -1
    const latch = new Map() // shot index -> the time its trip was first drawn
    let since = 0
    let lastNow = 0
    const perf = import.meta.env.DEV ? (window.__c3dFrames = window.__c3dFrames || []) : null
    const trips = import.meta.env.DEV ? (window.__c3dTrips = []) : null // dev only: when each element first drew tripped
    const paint = (now) => {
      const at = shotAt(shots, now - fx.startedAt)
      if (!at) return
      if (at.i !== shownI) {
        shownI = at.i
        since = now
        setShown({ el: at.shot.el, cascade })
      }
      const { W, H, dpr } = fitCanvas(cv)
      let u = at.u
      let A = at.shot.approach
      if (reduced) {
        // two stills per element: over its rating from its approach, tripped from its trip (the map's t0)
        u = u >= 0 ? POST_U : PRE_U
        A = 0
      } else if (u >= 0) {
        // the trip is latched to the first frame drawn at or past its t0: when the page stalls right at a
        // trip (the store's step change re-renders the app on that frame; the map stalls too), the panel
        // still plays the flashover and the breaker from their start instead of skipping past them
        if (!latch.has(at.i)) latch.set(at.i, now)
        u = now - latch.get(at.i)
      }
      const tSec = reduced ? 0 : now / 1000
      const alpha = reduced ? 1 : Math.min(1, (now - since) / 140)
      const st = drawScene(ctx, W, H, dpr, at.shot.el, u, A, tSec, { alpha, pal })
      frameRef.current = { key: at.shot.el.key, u, A, tSec }
      if (trips && st.status !== 'over' && st.status !== 'rising' && st.status !== 'wind' && !trips.some((x) => x.key === at.shot.el.key))
        trips.push({ key: at.shot.el.key, name: at.shot.el.name, ms: Math.round(now - fx.startedAt), t0: Math.round(at.shot.t0), latched: latch.has(at.i) ? Math.round(latch.get(at.i) - fx.startedAt) : null })
      readout(figRef.current, tagRef.current, st)
    }
    if (reduced) {
      // no animation loop: a timer at each approach's start and at each trip (2 ms late, so the trip has landed)
      const times = shots.flatMap((s) => [s.start, s.t0])
      const timers = times.map((t) => setTimeout(() => paint(performance.now()), Math.max(0, t + 2 - (performance.now() - fx.startedAt))))
      paint(performance.now())
      return () => timers.forEach(clearTimeout)
    }
    const loop = (now) => {
      if (perf && lastNow) {
        perf.push(now - lastNow)
        if (perf.length > 2000) perf.splice(0, perf.length - 2000)
      }
      lastNow = now
      // the clock is read when drawing, as the map's blast canvas reads it (CascadeFX), not the frame's
      // start time: a slow frame elsewhere on the page must not leave the panel behind the map
      const t = performance.now()
      paint(t)
      // dev only: what the panel's own drawing costs per frame (the intervals above include the whole page)
      if (perf) (window.__c3dCost = window.__c3dCost || []).push(performance.now() - t)
      if (t - fx.startedAt < fx.schedule.total + 100) raf = requestAnimationFrame(loop)
    }
    raf = requestAnimationFrame(loop)
    return () => cancelAnimationFrame(raf)
  }, [live, hidden, covered, fx, shots, reduced, cascade])

  // stopped: that element, drawn once (and again when the panel's size changes): the loop's last frame
  // when it shows the same element in the same state (so it looks frozen), else a still of it
  useLayoutEffect(() => {
    if (!visible || !stopEl) return undefined
    const cv = canvasRef.current
    if (!cv) return undefined
    const pal = readPalette()
    const f = frameRef.current
    const same = !!f && f.key === stopEl.key && (stopPre ? f.u < 0 : f.u >= 0)
    const [u, A, tSec] = same ? [f.u, f.A, f.tSec] : [stopPre ? PRE_U : POST_U, 0, 0]
    const draw = () => {
      const { W, H, dpr } = fitCanvas(cv)
      const st = drawScene(cv.getContext('2d'), W, H, dpr, stopEl, u, A, tSec, { pal })
      readout(figRef.current, tagRef.current, st)
    }
    draw()
    const ro = typeof ResizeObserver !== 'undefined' ? new ResizeObserver(draw) : null
    ro?.observe(cv)
    return () => ro?.disconnect()
  }, [visible, stopEl, stopPre])

  // where it floats: over the lower part of the left column, its bottom on the column's own bottom
  // (just above the timeline), the column's width (measured; phones dock it in the page flow, in CSS).
  // Measured again when a cover lifts: the presentation moves the page up while it is open.
  const [pos, setPos] = useState(null)
  useLayoutEffect(() => {
    if (dock !== 'float' || !visible) return undefined
    const place = () => {
      if (window.innerWidth <= 860) return setPos(null)
      const vh = window.innerHeight
      const colEl = document.querySelector('.mc-left')
      const col = colEl?.getBoundingClientRect()
      const foot = document.querySelector('.mc-bottom')?.getBoundingClientRect()
      const floor = foot && foot.height ? foot.top - 12 : vh - 140
      if (col && col.width) {
        const maxH = parseFloat(window.getComputedStyle(colEl).maxHeight)
        const colBottom = Number.isFinite(maxH) ? col.top + maxH : col.bottom
        return setPos({ left: Math.round(col.left), bottom: Math.round(vh - Math.min(floor, colBottom)), width: Math.round(col.width) })
      }
      setPos({ left: 16, bottom: Math.round(vh - floor), width: 320 })
    }
    place()
    window.addEventListener('resize', place)
    const watch = ['.mc-bottom', '.mc-left'].map((s) => document.querySelector(s)).filter(Boolean)
    const ro = watch.length && typeof ResizeObserver !== 'undefined' ? new ResizeObserver(place) : null
    watch.forEach((e) => ro?.observe(e))
    return () => {
      window.removeEventListener('resize', place)
      ro?.disconnect()
    }
  }, [dock, visible, covered])

  if (!visible) return null
  const el = view.el
  const close = () => {
    writeHidden()
    setHidden(true)
  }
  return (
    <PanelFrame
      el={el}
      canvasRef={canvasRef}
      figRef={figRef}
      tagRef={tagRef}
      onClose={close}
      className={`c3d c3d--${dock}${ended ? ' c3d--ending' : ''}`}
      style={dock === 'float' && pos ? { left: pos.left, bottom: pos.bottom, width: pos.width } : undefined}
      reviewHide={dock === 'float'}
    />
  )
}

/** The panel's markup: the drawing with its kind over it, then the name, the loading, why, and the small print. */
export function PanelFrame({ el, canvasRef, figRef, tagRef, onClose, className = 'c3d c3d--inline', style, reviewHide = false }) {
  const why = whyLine(el)
  const past = precedent(el)
  return (
    <section className={className} style={style} aria-label="What fails, drawn in 3D" data-review-hide={reviewHide ? '' : undefined}>
      <div className="c3d__stage">
        <canvas ref={canvasRef} className="c3d__canvas" role="img" aria-label={`${el.name}. ${why}`} />
        <p className="c3d__kind">
          {kindLine(el)}
          {el.more > 0 && <span className="c3d__more"> +{el.more.toLocaleString('en-US')} more</span>}
        </p>
        {onClose && (
          <button type="button" className="c3d__close" aria-label="Hide the 3D view" title="Hide the 3D view" onClick={onClose}>
            ×
          </button>
        )}
      </div>
      <div className="c3d__body">
        <p className="c3d__name">{el.name}</p>
        <p className="c3d__load">
          <span className="c3d__lbl">Loading</span> <b className="c3d__fig" ref={figRef} /> <span className="c3d__lbl">of its rating</span>
          <span className="c3d__tag" ref={tagRef} />
        </p>
        <p className="c3d__why">{why}</p>
        <p className="c3d__fine">Synthetic grid model · a typical tower, not the real one</p>
        {past && (
          <p className="c3d__fine c3d__past">
            {past}{' '}
            <a href={REPORT_URL} target="_blank" rel="noreferrer" title="U.S.-Canada Power System Outage Task Force, final report on the August 14, 2003 blackout (April 2004)">
              Source
            </a>
          </p>
        )}
      </div>
    </section>
  )
}
