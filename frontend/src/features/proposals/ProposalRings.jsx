// The five real, planned Florida data centers (data/florida_five.json) as small neutral rings on the Watch it
// fail map, so the first thing a visitor sees is real proposals they can test. Each ring carries a short label
// in the sources' own words ("Fort Meade · 1,200 MW reported · announced"); hover shows who reported it (links);
// a click drops a campus of the reported size at the reported place, the same path as the "Planned data
// centers" dropdown (proposalStore.useDropProposal); Ctrl/Cmd+click adds one more data center of the current
// size there, like Ctrl+click anywhere on the map. Every test runs on the SYNTHETIC grid model.
//
// When: Florida only (nothing is fetched: the file ships with the app), data-center mode, after the opening
// moment. Full before anything is dropped; faded (no labels, clickable only on the ring itself) once a case is
// on the map; faded and click-through while the danger zones or the headroom heatmap are on (their zones and
// dots are what a click there means); gone while a cascade computes or plays. Strengthen hides the demo's
// layers (App.jsx, where the rings are drawn first so every other layer sits above them).
//
// Keyboard and screen readers use the "Planned data centers" dropdown (the same drop, with its source link):
// the rings sit inside the map's role="img" drawing and would otherwise be the page's first five tab stops,
// ahead of the top bar.
//
// A GridMap child: SVG inside the camera, sizes divided by the zoom like the other layers so the rings stay the
// same size on screen. Labels avoid the town names, each other, the floating panels and the map's edge.
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import floridaFive from '../../data/florida_five.json'
import { useMapView } from '../../GridMap'
import { citiesFor, fmt } from '../../geo'
import { useOverload } from '../../store'
import { useDangerUi } from '../danger/dangerStore'
import { useIntroPhase } from '../flow/introPhase'
import { isDroppedAt, sizeOf, sourceHost, statusOf, useDropProposal, usePickedProposal } from './proposalStore'
import './proposals.css'

// the name on the map: the project as the reports name it, shortened (the tooltip gives the full name)
const SHORT = {
  'stonebridge-fort-meade': 'Fort Meade',
  'atlas-compute-fort-pierce': 'Atlas Compute',
  'sentinel-grove-fort-pierce': 'Sentinel Grove',
  'pba-holdings-loxahatchee': 'Project Tango',
  'nextnrg-near-jacksonville-international-airport': 'NextNRG',
}

// sizes in screen-constant units (map units at zoom 1; drawn divided by the zoom)
const R = 6.5 // the ring (the campus ring is 16, a danger zone 7 to 30)
const HIT = 12 // the clickable circle around it before anything is dropped
const HIT_FADED = R + 1 // once a case is on the map: only the ring itself, so a click beside it places a campus there
const FAN = 9 // proposals reported at the same place fan out this far from it, one above the other
const GAP = 5 // ring edge to label
const PAD_X = 5
const F1 = 11 // label line 1: the name
const F2 = 9.5 // label line 2: size and status
const LINE1 = 4 + 9 // plaque top to line 1's baseline
const LINE2 = LINE1 + 12
const PLAQUE_H = LINE2 + 4.5
const MIN_SCALE = 0.95 // screen px per map unit below which the labels are too small to read (phones): rings only,
// drawn bigger there (never under the desktop size on screen) so they stay visible and a finger can hit them

const ENTRIES = floridaFive.entries.filter((e) => Number.isFinite(e.lat) && Number.isFinite(e.lon) && e.mw > 0)
const line2Of = (e) => `${sizeOf(e)} reported · ${statusOf(e)}`
const hostsOf = (e) => [...new Map((e.sources || []).filter((s) => s.url).map((s) => [sourceHost(s.url), s])).values()]
const quiet = (e) => /paus|cancel|reject|withdraw/i.test(`${e.status} ${e.status_reported || ''}`)

export default function ProposalRings() {
  const { k, project } = useMapView()
  const o = useOverload()
  const { grid, region, mode, site, extraSites, trip, cascade, cascading, playing, fx, headroomOn, place } = o
  const drop = useDropProposal()
  const pickedId = usePickedProposal()
  const dangerOn = useDangerUi().on
  const intro = useIntroPhase()
  const florida = region === 'FL' && (grid?.meta?.region || 'FL') === 'FL' && !!grid?.subs?.length
  const replaying = !!(cascading || playing || fx)
  const shown = florida && mode === 'campus' && !replaying && intro !== 'gate' && intro !== 'wait' && intro !== 'play'
  // the danger zones and the headroom heatmap answer "where": a click there means their zone or dot, not a proposal
  const inert = !!(dangerOn || headroomOn)
  const faded = !!(site || extraSites.length || trip.length || cascade || inert)
  const [frame, setFrame] = useState(null) // {scale (screen px per map unit at zoom 1), map, blocks} in map units
  const grow = frame ? Math.max(1, 1 / frame.scale) : 1

  const gRef = useRef(null)
  const [hover, setHover] = useState(null) // id under the pointer
  const [tip, setTip] = useState(null) // {id, rect: the client rect of the ring with its label}
  const closeTimer = useRef(null)

  // where things are on screen, in map units: the map's visible box and the panels floating over it
  const measure = useCallback(() => {
    const g = gRef.current
    const svg = g?.ownerSVGElement
    const ctm = g?.getScreenCTM()
    const base = svg?.getScreenCTM()
    if (!ctm || !base) return
    const inv = ctm.inverse()
    const toMap = (r) => {
      const a = new DOMPoint(r.left, r.top).matrixTransform(inv)
      const b = new DOMPoint(r.right, r.bottom).matrixTransform(inv)
      return { x0: Math.min(a.x, b.x), y0: Math.min(a.y, b.y), x1: Math.max(a.x, b.x), y1: Math.max(a.y, b.y) }
    }
    const box = svg.parentElement?.getBoundingClientRect() || svg.getBoundingClientRect()
    const vis = {
      left: Math.max(box.left, 0),
      top: Math.max(box.top, 0),
      right: Math.min(box.right, window.innerWidth),
      bottom: Math.min(box.bottom, window.innerHeight),
    }
    const blocks = []
    document.querySelectorAll('.appbar, .mc-top > *, .mc-left, .mc-right, .mc-bottom, .mc-credit > *, .map-controls').forEach((el) => {
      const r = el.getBoundingClientRect()
      if (r.width < 1 || r.height < 1) return
      if (r.right <= vis.left || r.left >= vis.right || r.bottom <= vis.top || r.top >= vis.bottom) return
      blocks.push(toMap(r))
    })
    setFrame({ scale: base.a, map: toMap(vis), blocks })
  }, [])

  useLayoutEffect(() => {
    if (!shown) return undefined
    measure()
    const late = setTimeout(measure, 950) // after a camera ease (GridMap's 900 ms)
    return () => clearTimeout(late)
  }, [shown, k, grid, measure])
  useEffect(() => {
    if (!shown) return undefined
    const svg = gRef.current?.ownerSVGElement
    let t = null
    const soon = () => {
      clearTimeout(t)
      t = setTimeout(measure, 60)
    }
    window.addEventListener('resize', soon)
    svg?.addEventListener('pointerup', soon) // the end of a pan
    return () => {
      clearTimeout(t)
      window.removeEventListener('resize', soon)
      svg?.removeEventListener('pointerup', soon)
    }
  }, [shown, measure])

  // each ring's place: the reported point, fanned out when several are reported at the same place
  const rings = useMemo(() => {
    if (!florida) return []
    const groups = new Map()
    ENTRIES.forEach((e) => {
      const key = `${e.lat.toFixed(3)},${e.lon.toFixed(3)}`
      groups.set(key, [...(groups.get(key) || []), e])
    })
    return ENTRIES.map((e) => {
      const group = groups.get(`${e.lat.toFixed(3)},${e.lon.toFixed(3)}`)
      const i = group.indexOf(e)
      const [x, y] = project(e.lon, e.lat)
      return { e, x, y, oy: (i - (group.length - 1) / 2) * 2 * FAN * grow }
    })
    // project changes with the region's projection, which comes with a new grid
  }, [florida, project, grid, grow]) // eslint-disable-line react-hooks/exhaustive-deps

  const labels = useMemo(
    () => (shown && !faded && frame && frame.scale >= MIN_SCALE ? placeLabels(rings, k, frame, grid, project) : {}),
    [shown, faded, frame, rings, k, grid, project],
  )

  // the tooltip: opens on hover, stays while the pointer is on it, Escape or a click closes it
  const open = (id, el) => {
    clearTimeout(closeTimer.current)
    setHover(id)
    // beside the ring AND its label, so the card never covers the label (a click there drops the campus)
    const r = el?.getBoundingClientRect()
    if (r) setTip({ id, rect: { left: r.left, top: r.top, right: r.right, bottom: r.bottom } })
  }
  const closeSoon = () => {
    clearTimeout(closeTimer.current)
    closeTimer.current = setTimeout(() => {
      setHover(null)
      setTip(null)
    }, 180)
  }
  const closeNow = useCallback(() => {
    clearTimeout(closeTimer.current)
    setHover(null)
    setTip(null)
  }, [])
  useEffect(() => () => clearTimeout(closeTimer.current), [])
  // the rings stop taking clicks (danger zones, heatmap) or go (a cascade): a card left open would point at
  // nothing, so it closes then (adjusted during render, React's pattern for state that follows a prop)
  const live = shown && !inert
  const [wasLive, setWasLive] = useState(live)
  if (live !== wasLive) {
    setWasLive(live)
    if (!live) {
      setHover(null)
      setTip(null)
    }
  }
  useEffect(() => {
    if (!tip) return undefined
    const onKey = (ev) => ev.key === 'Escape' && closeNow()
    const svg = gRef.current?.ownerSVGElement
    window.addEventListener('keydown', onKey)
    svg?.addEventListener('wheel', closeNow, { passive: true }) // the camera moves: the tooltip would be left behind
    return () => {
      window.removeEventListener('keydown', onKey)
      svg?.removeEventListener('wheel', closeNow)
    }
  }, [tip, closeNow])
  if (!shown || !rings.length) return null
  // a click drops this proposal (its reported size); Ctrl/Cmd+click is the map's "one more data center of the
  // current size" (store.place with multi), placed at the proposal's reported point instead of the pointer
  const pick = (e, ev) => {
    closeNow()
    if (ev?.ctrlKey || ev?.metaKey) place(e.lat, e.lon, { multi: true })
    else drop(e)
  }
  const tipEntry = live && tip && ENTRIES.find((e) => e.id === tip.id)

  return (
    <g ref={gRef} className={`pr-layer${faded ? ' pr-layer--faded' : ''}${inert ? ' pr-layer--inert' : ''}`} aria-hidden="true">
      {rings.map(({ e, x, y, oy }) => {
        const cy = y + oy / k
        const l = labels[e.id]
        const active = pickedId === e.id && isDroppedAt(site, e)
        const cls = `pr${hover === e.id ? ' pr--hover' : ''}${active ? ' pr--active' : ''}${quiet(e) ? ' pr--paused' : ''}`
        return (
          <g
            key={e.id}
            className={cls}
            data-id={e.id}
            onPointerDown={(ev) => ev.stopPropagation()} // not a map click (no pan, no drop at the pointer): the ring's own click decides
            onPointerUp={(ev) => ev.stopPropagation()}
            onClick={(ev) => {
              ev.stopPropagation()
              pick(e, ev)
            }}
            onPointerEnter={(ev) => open(e.id, ev.currentTarget)}
            onPointerLeave={closeSoon}
          >
            <circle className="pr-hit" cx={x} cy={cy} r={((faded ? HIT_FADED : HIT) * grow) / k} />
            <circle className="pr-ring" cx={x} cy={cy} r={(R * grow) / k} strokeWidth={(1.4 * grow) / k} pathLength={24} />
            <circle className="pr-dot" cx={x} cy={cy} r={(1.6 * grow) / k} />
            {l && (
              <g className="pr-label">
                <rect className="pr-plaque" x={l.x0 / k} y={l.y0 / k} width={l.w / k} height={PLAQUE_H / k} rx={3 / k} strokeWidth={1 / k} />
                <text className="pr-name" x={(l.x0 + PAD_X) / k} y={(l.y0 + LINE1) / k} fontSize={F1 / k}>
                  {SHORT[e.id] || e.name}
                </text>
                <text className="pr-meta" x={(l.x0 + PAD_X) / k} y={(l.y0 + LINE2) / k} fontSize={F2 / k}>
                  {line2Of(e)}
                </text>
              </g>
            )}
          </g>
        )
      })}
      {tipEntry &&
        createPortal(
          <Tip e={tipEntry} rect={tip.rect} onEnter={() => clearTimeout(closeTimer.current)} onLeave={closeSoon} />,
          document.body,
        )}
    </g>
  )
}

// The hover card: what was reported, by whom (links), and what a click does. Fixed to the viewport beside the
// ring and its label, flipped to stay on screen.
function Tip({ e, rect, onEnter, onLeave }) {
  const ref = useRef(null)
  const [pos, setPos] = useState({ left: rect.right + 10, top: rect.top - 10, ready: false })
  useLayoutEffect(() => {
    const el = ref.current
    if (!el) return
    const w = el.offsetWidth
    const h = el.offsetHeight
    const vw = window.innerWidth
    const vh = window.innerHeight
    const fits = (x) => x >= 8 && x + w <= vw - 8
    let left = rect.right + 12 // beside it, right then left
    let top = (rect.top + rect.bottom) / 2 - 22
    if (!fits(left)) left = rect.left - 12 - w
    if (!fits(left)) {
      // no room beside it (a phone): centered under the ring, or over it
      left = Math.max(8, Math.min(vw - w - 8, (rect.left + rect.right) / 2 - w / 2))
      top = rect.bottom + 10 + h > vh - 8 ? rect.top - 10 - h : rect.bottom + 10
    }
    top = Math.max(8, Math.min(vh - h - 8, top))
    // measuring the card before paint is what a layout effect is for; one re-render, before paint
    // oxlint-disable-next-line react/set-state-in-effect
    setPos({ left, top, ready: true })
  }, [rect, e])
  const hosts = hostsOf(e)
  return (
    <div
      ref={ref}
      className={`pr-tip${pos.ready ? ' pr-tip--ready' : ''}`}
      style={{ left: pos.left, top: pos.top }}
      role="tooltip"
      onPointerEnter={onEnter}
      onPointerLeave={onLeave}
      // a portal still bubbles through React to the map's own handlers: a press here is not a map click
      onPointerDown={(ev) => ev.stopPropagation()}
      onPointerUp={(ev) => ev.stopPropagation()}
      onClick={(ev) => ev.stopPropagation()}
    >
      <p className="pr-tip__name">{e.name}</p>
      <p className="pr-tip__meta">
        {e.place} · {sizeOf(e)} reported{e.size_note ? ` (${e.size_note})` : ''} · {statusOf(e)}
      </p>
      {hosts.length > 0 && (
        <p className="pr-tip__src">
          As reported by{' '}
          {hosts.map((s, i) => (
            <span key={s.url}>
              {i > 0 && (i === hosts.length - 1 ? ' and ' : ', ')}
              <a href={s.url} target="_blank" rel="noreferrer" title={s.title}>
                {sourceHost(s.url)}
              </a>
            </span>
          ))}
        </p>
      )}
      <p className="pr-tip__act">Click to test a campus of {e.mw_reported ? `the full ${fmt(e.mw)} MW` : 'this reported size'} here.</p>
      <p className="pr-tip__fine">On a synthetic grid model: not a prediction about the real project or utility.</p>
    </div>
  )
}

// ------------------------------------------------------------------ label placement
// Everything in "zoomed units" (map units x zoom = screen-constant sizes). Each label tries the sides of its
// ring (the side toward the middle of the map first) and takes the spot that covers the least of the town
// names, the other labels and rings, the panels and the map's edge; a label with no clean spot is left out.
let measureCtx = null
function textWidth(text, size, weight) {
  try {
    measureCtx = measureCtx || document.createElement('canvas').getContext('2d')
    const family = getComputedStyle(document.documentElement).getPropertyValue('--font') || 'system-ui, sans-serif'
    measureCtx.font = `${weight} ${size}px ${family}`
    return measureCtx.measureText(text).width
  } catch {
    return text.length * size * 0.56
  }
}

const area = (a, b) => Math.max(0, Math.min(a.x1, b.x1) - Math.max(a.x0, b.x0)) * Math.max(0, Math.min(a.y1, b.y1) - Math.max(a.y0, b.y0))

function placeLabels(rings, k, frame, grid, project) {
  const zoomed = (r) => ({ x0: r.x0 * k, y0: r.y0 * k, x1: r.x1 * k, y1: r.y1 * k })
  const map = zoomed(frame.map)
  const blocks = frame.blocks.map(zoomed)
  const towns = (grid ? citiesFor(grid) : []).map((c) => {
    const [x, y] = project(c.lon, c.lat)
    return { x0: x * k + 4, x1: x * k + 8 + textWidth(c.name, 11, 500), y0: y * k - 4 - 12, y1: y * k + 1 } // with a little margin
  })
  const dots = rings.map(({ x, y, oy }) => ({ x0: x * k - HIT, x1: x * k + HIT, y0: y * k + oy - HIT, y1: y * k + oy + HIT }))
  const midX = ((frame.map.x0 + frame.map.x1) / 2) * k
  const placed = []
  const out = {}
  rings.forEach(({ e, x, y, oy }, idx) => {
    const cx = x * k
    const cy = y * k + oy
    const w = Math.max(textWidth(SHORT[e.id] || e.name, F1, 600), textWidth(line2Of(e), F2, 400)) + PAD_X * 2 + 2
    const h = PLAQUE_H
    const right = (dy) => ({ x0: cx + R + GAP, y0: cy + dy })
    const left = (dy) => ({ x0: cx - R - GAP - w, y0: cy + dy })
    const sides = [
      [right(-h / 2), right(-h + 6), right(-6)],
      [left(-h / 2), left(-h + 6), left(-6)],
    ]
    const above = cy - R - GAP - h
    const below = cy + R + GAP
    const diagonals = [
      [
        { x0: cx - R, y0: below },
        { x0: cx - R, y0: above },
      ],
      [
        { x0: cx + R - w, y0: below },
        { x0: cx + R - w, y0: above },
      ],
    ]
    const inward = cx > midX ? [1, 0] : [0, 1] // the side toward the middle of the map first
    const ordered = [...sides[inward[0]], ...sides[inward[1]], { x0: cx - w / 2, y0: above }, { x0: cx - w / 2, y0: below }, ...diagonals[inward[0]], ...diagonals[inward[1]]]
    let best = null
    ordered.forEach((p, order) => {
      const b = { x0: p.x0, y0: p.y0, x1: p.x0 + w, y1: p.y0 + h }
      const outside = w * h - area(b, map)
      let cost = order * 2 + outside * 12
      blocks.forEach((o) => (cost += area(b, o) * 12))
      towns.forEach((o) => (cost += area(b, o) * 4))
      placed.forEach((o) => (cost += area(b, o) * 8))
      dots.forEach((o, j) => j !== idx && (cost += area(b, o) * 4))
      if (!best || cost < best.cost) best = { ...b, cost }
    })
    if (!best || best.cost > w * h * 0.6) return // no clean spot: the ring stands alone (its tooltip still names it)
    placed.push(best)
    out[e.id] = { x0: best.x0, y0: best.y0, w }
  })
  return out
}
