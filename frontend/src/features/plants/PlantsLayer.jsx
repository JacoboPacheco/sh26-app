// Plant Down on the map (SVG inside the camera, map units): every power plant as a small ring
// sized by its capacity. Quiet by default — a faint fuel tint — and bright only when it means
// something: the plant you picked, the fuel chip you're pointing at, a plant taken out (dark, with
// a cross). The picked plant's trace draws faint white arcs to the areas that run on it.
//
// Mount as a GridMap child: <PlantsLayer />. It shows in Plants mode (store.mode === 'plants');
// in any other mode only the plants taken out stay drawn, so a cascade elsewhere still explains
// itself. `always` shows it whatever the mode; `view` ({k, project}) replaces the map's context
// (the Preview, which portals the layer into the map from outside GridMap's tree).
import { memo, useEffect, useMemo, useRef } from 'react'
import { useMapView } from '../../GridMap'
import { useOverload } from '../../store'
import { fmt, fuelFamily, fuelLabel, pct, pretty } from './fuels'
import './plants.css'
import {
  caseWithRegion,
  ensurePlants,
  hoverPlant,
  liveOverload,
  regionOf,
  removedIds,
  resetPlants,
  selectPlant,
  traceBody,
  traceKey,
  usePlants,
} from './plantsStore'

// radius in screen-ish units: √capacity, so area tracks MW; grows a little as you zoom in
const radius = (pmax, k) => (1.1 + Math.sqrt(Math.max(pmax, 0)) * 0.17) / Math.pow(k, 0.72)
const MAX_ARCS = 6

export default function PlantsLayer({ view, always = false }) {
  const ctx = useMapView()
  const { k, project } = view || ctx
  const o = useOverload()
  const st = usePlants()
  const region = regionOf(o)
  // the projection the map is drawing right now (it follows the grid's region)
  // (only a two-letter code or 'US' counts: an older payload's meta.region is the dataset's own text)
  const mapRegion = [(view || ctx).region, o?.grid?.meta?.region].find((r) => /^[A-Z]{2}$/.test(String(r || ''))) || region
  const on = always || o?.mode === 'plants'

  useEffect(() => {
    liveOverload.current = o
  })
  useEffect(() => {
    if (on && region !== 'US') ensurePlants(region)
  }, [on, region])

  // "Start over" (store.resetAll): every plant goes back
  const resetCount = o?.resetCount
  const seenReset = useRef(resetCount)
  useEffect(() => {
    if (resetCount === seenReset.current) return
    seenReset.current = resetCount
    resetPlants()
  }, [resetCount])

  // only this region's plants, and only once the map is drawing this region
  const plants = st.region === region && mapRegion === region ? st.list.data?.plants || null : null
  const { outages, retireFuels, list } = st
  const removed = useMemo(() => removedIds({ outages, retireFuels, list }), [outages, retireFuels, list])
  // draw big plants first so small ones stay on top (and clickable)
  const glyphs = useMemo(
    () =>
      (plants || [])
        .filter((p) => Number.isFinite(p.lat) && Number.isFinite(p.lon))
        .slice()
        .sort((a, b) => b.pmax - a.pmax)
        .map((p) => {
          const [x, y] = project(p.lon, p.lat)
          return { p, x, y, family: fuelFamily(p.fuel) }
        }),
    // a new region's projection always comes with a new plant list (`plants` waits for both)
    [plants, project],
  )

  const selected = plants?.find((p) => p.id === st.selectedId) || null
  const hovered = plants?.find((p) => p.id === st.hoverId) || null
  const trace = selected ? st.traces[traceKey(region, selected.id, traceBody(caseWithRegion(o, region), st))] : null

  if (!plants || (!on && !removed.size)) return null
  const shown = on ? glyphs : glyphs.filter((g) => removed.has(g.p.id))

  return (
    // aria-hidden: the panel's plant list and ranking are the keyboard and screen-reader way in
    <g className={`pl-layer${on ? '' : ' pl-layer--quiet'}${st.fuelHover ? ' pl-layer--fuel' : ''}`} aria-hidden="true">
      {on && selected && trace?.status === 'done' && <TraceArcs plant={selected} trace={trace.data} grid={o?.grid} k={k} project={project} />}
      <Glyphs glyphs={shown} k={k} removed={removed} selectedId={st.selectedId} fuelHover={st.fuelHover} interactive={on} />
      {on && hovered && <HoverLabel plant={hovered} out={removed.has(hovered.id)} k={k} project={project} />}
    </g>
  )
}

// The rings (a few hundred), memoized so a hover label or an arc doesn't re-render them.
const Glyphs = memo(function Glyphs({ glyphs, k, removed, selectedId, fuelHover, interactive }) {
  const sw = 1.1 / k
  const stop = (e) => e.stopPropagation() // a click on a plant picks it; it doesn't drop a data center
  return (
    <g className="pl-glyphs">
      {glyphs.map(({ p, x, y, family }) => {
        const r = radius(p.pmax, k)
        const out = removed.has(p.id)
        const sel = p.id === selectedId
        const lit = fuelHover != null && p.fuel === fuelHover
        const cls = `pl pl--${family}${sel ? ' pl--sel' : ''}${out ? ' pl--out' : ''}${lit ? ' pl--lit' : ''}`
        const c = r * 0.62
        return (
          <g
            key={p.id}
            className={cls}
            transform={`translate(${x} ${y})`}
            onPointerDown={interactive ? stop : undefined}
            onPointerUp={interactive ? stop : undefined}
            onClick={interactive ? () => selectPlant(p.id) : undefined}
            onPointerEnter={interactive ? () => hoverPlant(p.id) : undefined}
            onPointerLeave={interactive ? () => hoverPlant(null) : undefined}
          >
            {interactive && <circle className="pl-hit" r={Math.max(r + 2 / k, 6 / k)} />}
            {sel && <circle className="pl-halo" r={r + 5 / k} strokeWidth={sw} />}
            <circle className="pl-ring" r={r} strokeWidth={sel ? sw * 1.6 : sw} />
            {out && <path className="pl-cross" d={`M${-c} ${-c}L${c} ${c}M${-c} ${c}L${c} ${-c}`} strokeWidth={sw * 1.5} />}
          </g>
        )
      })}
    </g>
  )
})

// "Bartow 6 · gas · 2,799 MW" beside the plant under the pointer, at constant size on screen.
function HoverLabel({ plant, out, k, project }) {
  const [x, y] = project(plant.lon, plant.lat)
  const fs = 11 / k
  const text = `${pretty(plant.sub_name || plant.name)} · ${fuelLabel(plant.fuel).toLowerCase()} · ${fmt(plant.pmax)} MW${out ? ' · out' : ''}`
  const w = text.length * fs * 0.56 + 10 / k
  const lx = x + radius(plant.pmax, k) + 5 / k
  const ly = y - 9 / k
  return (
    <g className="pl-label" pointerEvents="none">
      <rect x={lx} y={ly} width={w} height={17 / k} rx={4 / k} />
      <text x={lx + 5 / k} y={ly + 12 / k} fontSize={fs}>
        {text}
      </text>
    </g>
  )
}

// Where the plant's power goes: a faint arc to each area it serves (its biggest shares), width by
// MW, labeled with the share of that area's power it supplies. The backend places each area at the
// middle of its substations; an older answer without coordinates falls back to the drawable grid's.
function TraceArcs({ plant, trace, grid, k, project }) {
  const centers = useMemo(() => {
    const acc = new Map()
    ;(grid?.subs || []).forEach((s) => {
      const w = Math.max(s.load_mw || 0, 0.1)
      const a = acc.get(s.area) || { lon: 0, lat: 0, w: 0 }
      a.lon += s.lon * w
      a.lat += s.lat * w
      a.w += w
      acc.set(s.area, a)
    })
    return new Map([...acc].map(([area, a]) => [area, [a.lon / a.w, a.lat / a.w]]))
  }, [grid])
  const [px, py] = project(plant.lon, plant.lat)
  const serves = (trace?.serves || [])
    .map((s) => ({ ...s, at: Number.isFinite(s.lat) && Number.isFinite(s.lon) ? [s.lon, s.lat] : centers.get(s.area) }))
    .filter((s) => s.at)
    .slice(0, MAX_ARCS)
  const maxMw = Math.max(1, ...serves.map((s) => s.mw || 0))
  const arcs = serves.map((s) => {
    const [ax, ay] = project(s.at[0], s.at[1])
    return { ...s, ax, ay, text: `${s.area} ${pct(s.share)}` }
  })
  const labels = placeLabels(arcs, k)
  return (
    <g className="pl-arcs" pointerEvents="none">
      {arcs.map((s, i) => {
        const { ax, ay } = s
        const d = Math.hypot(ax - px, ay - py)
        const w = (0.7 + 2.2 * Math.sqrt((s.mw || 0) / maxMw)) / k
        // bow each arc a little to one side so arcs to nearby towns don't overlap
        const bow = Math.min(d * 0.22, 40)
        const cx = d < 1 ? px : (px + ax) / 2 - ((ay - py) / d) * bow
        const cy = d < 1 ? py : (py + ay) / 2 + ((ax - px) / d) * bow
        const l = labels[i]
        return (
          <g key={s.area} className="pl-arc" style={{ animationDelay: `${i * 90}ms` }}>
            {d >= 1 && <path d={`M${px} ${py}Q${cx} ${cy} ${ax} ${ay}`} strokeWidth={w} pathLength={1} />}
            <circle cx={ax} cy={ay} r={2.4 / k} />
            {l.moved && <line className="pl-arc__lead" x1={ax - 3 / k} y1={ay} x2={l.x + 1 / k} y2={l.y - 4 / k} strokeWidth={0.8 / k} />}
            {/* left of the dot: the map's own town labels sit to the right */}
            <text x={l.x} y={l.y} fontSize={10.5 / k} strokeWidth={3 / k} textAnchor="end">
              {s.text}
            </text>
          </g>
        )
      })}
    </g>
  )
}

// Labels left of their dots, nudged down (in screen pixels) until no two overlap: areas near one
// another (Hollywood, Opa Locka…) would otherwise print on top of each other.
const LABEL_H = 14
function placeLabels(arcs, k) {
  const placed = []
  const out = new Array(arcs.length)
  arcs
    .map((a, i) => ({ i, sx: a.ax * k - 5, sy: a.ay * k + 4, w: a.text.length * 6.2 }))
    .sort((p, q) => p.sy - q.sy)
    .forEach((l) => {
      const y0 = l.sy
      const hits = (p) => l.sx - l.w < p.sx && p.sx - p.w < l.sx && Math.abs(p.sy - l.sy) < LABEL_H
      for (let n = 0; n < 10 && placed.some(hits); n++) l.sy += LABEL_H
      placed.push(l)
      out[l.i] = { x: l.sx / k, y: l.sy / k, moved: l.sy !== y0 }
    })
  return out
}
