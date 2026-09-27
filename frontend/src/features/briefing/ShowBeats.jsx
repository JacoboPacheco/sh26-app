import { useEffect, useMemo } from 'react'
import { useOverload } from '../../store'
import { areaShape, branchAt } from './beatMaps'
import { AREA } from './showDeck'
import { P, moneyShort } from './showText'
import { useElapsed } from './useShowClock'

// The map pictures of the presentation's other beats, each its own (PRESENT V2: "each step in the presentation should
// have a different visual ... points to where the problem actually is"). Each is a component that draws a layer on
// the live map while its slide is up and takes it away after; the slide card around it is the Slide's own.

// ------------------------------------------------------------------ the areas: the camera visits each one in turn
// Each named area (the report's, most people first) is outlined and hatched as the camera arrives; the ones already
// visited stay, dimmed; the slide's bars light the one on screen. Paused: every area outlined, the top three named.
export function AreaTour({ report, animate, stage, onArea }) {
  const { grid } = useOverload()
  const areas = useMemo(() => (report?.areas || []).filter((a) => Number(a.people) > 0).slice(0, AREA.max), [report])
  const shapes = useMemo(() => areas.map((a) => ({ a, ...areaShape({ grid }, a) })).filter((s) => s.pts.length), [areas, grid])
  const clock = useElapsed(animate && shapes.length > 0, AREA.intro + shapes.length * AREA.step + 1200)
  const cur = !animate ? -1 : clock < AREA.intro ? -1 : Math.min(shapes.length - 1, Math.floor((clock - AREA.intro) / AREA.step))
  useEffect(() => {
    if (!stage || !shapes.length) return
    if (!animate) {
      stage.camera({ points: shapes.flatMap((s) => s.pts) })
      stage.layer({
        key: 'areas-still',
        still: true,
        hulls: shapes.map((s) => ({ key: s.a.area, pts: s.pts, tone: 'lost' })),
      })
      onArea?.(null)
      return
    }
    if (cur < 0) {
      stage.camera({ points: shapes.flatMap((s) => s.pts) })
      stage.layer({ key: 'areas', hulls: [] })
      return
    }
    const s = shapes[cur]
    stage.camera({ points: s.pts, center: s.center })
    stage.layer({
      key: 'areas',
      hulls: shapes.slice(0, cur + 1).map((x, i) => ({ key: x.a.area, pts: x.pts, tone: i === cur ? 'lost' : 'dim' })),
    })
    onArea?.(String(s.a.area).toLowerCase())
  }, [animate, cur, shapes, stage, onArea])
  useEffect(() => () => stage?.layer(null), [stage])
  return null
}

// ------------------------------------------------------------------ one layer for a slide's life
function useLayer(stage, layer, camera) {
  useEffect(() => {
    if (!stage || !layer) return
    if (camera) stage.camera(camera)
    stage.layer(layer)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stage, layer])
  useEffect(() => () => stage?.layer(null), [stage])
}

// the trigger: the campus on the map, with its size and the hour (full deck: what happened)
export function EventLayer({ report, lang, stage, animate }) {
  const O = useOverload()
  const T = P[lang]
  const c = report?.case || {}
  const { cascade, subPos, branchById } = O
  const layer = useMemo(() => {
    if (c.sub_lon != null && Number(c.mw) > 0) {
      const at = [c.sub_lon, c.sub_lat]
      return { key: 'event', still: !animate, marks: [{ at, tone: 'site', r: 18 }], tags: [{ at, text: T.triggerTag(c.mw), sub: c.sub_area || '', tone: 'info', delay: animate ? 500 : 0 }] }
    }
    const first = (cascade?.steps || []).find((st) => st.n === 0)
    const ids = (first?.tripped || []).slice(0, 500)
    return ids.length ? { key: 'event', still: !animate, lines: ids.map((id) => ({ id, tone: 'cut' })) } : null
  }, [c.sub_lon, c.sub_lat, c.mw, c.sub_area, cascade, animate, T])
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const cam = useMemo(() => (c.sub_lon != null ? { points: [[c.sub_lon, c.sub_lat]], center: [c.sub_lon, c.sub_lat] } : null), [c.sub_lon, c.sub_lat, subPos, branchById])
  useLayer(stage, layer, cam)
  return null
}

// what the storm cut: every line it knocked out, dashed red (the cause of a storm's outage)
export function StormLayer({ report, stage, animate }) {
  const { cascade } = useOverload()
  const layer = useMemo(() => {
    const first = (cascade?.steps || []).find((st) => st.n === 0)
    const ids = (first?.tripped || report?.replay?.trip || []).slice(0, 600)
    return ids.length ? { key: 'storm', still: !animate, lines: ids.map((id) => ({ id, tone: 'cut' })) } : null
  }, [cascade, report, animate])
  useLayer(stage, layer, { type: 'region' })
  return null
}

// CLEAR AREAS (user, Sat 20:30): a figure that names an area sits outside it, level with its edge on the side away from
// the other areas (the west edge of an area west of the group's middle, else the east edge), on a short leader line: the
// area stays a clear shape. When the text on that side would lie over another area (`others`: their points), the other
// side is used if it is clearer. `room` = a point beyond the text, so the camera frames the tag too.
function outside(O, a, mid, span, others = []) {
  const { pts, center } = areaShape(O, a)
  const all = pts.length ? pts : center ? [center] : []
  if (!all.length) return null
  const c = center || [all.reduce((n, p) => n + p[0], 0) / all.length, all.reduce((n, p) => n + p[1], 0) / all.length]
  const pad = Math.max(0.45, span * 1.5) // about a tag's width at the zoom that frames the areas
  const place = (west) => {
    const edge = all.reduce((m, p) => ((west ? p[0] < m[0] : p[0] > m[0]) ? p : m), all[0])
    // the text's box beside the edge (a little taller than two lines, a little wider than the text: the hatching is
    // drawn around the substations, past their points)
    const x0 = west ? edge[0] - pad * 1.1 : edge[0]
    const x1 = west ? edge[0] : edge[0] + pad * 1.1
    const h = pad * 0.4
    const hits = others.reduce((n, p) => n + (p[0] > x0 && p[0] < x1 && Math.abs(p[1] - c[1]) < h ? 1 : 0), 0)
    return { at: [edge[0], c[1]], side: west ? 'w' : 'e', room: [edge[0] + (west ? -pad : pad), c[1]], hits }
  }
  const pref = place(c[0] <= mid)
  const alt = pref.hits ? place(!(c[0] <= mid)) : null
  const best = alt && alt.hits < pref.hits ? alt : pref
  return { at: best.at, side: best.side, room: best.room, pts: all }
}

// the middle and the east-west span of a set of areas (their centers)
function spanOf(areas) {
  const xs = areas.map((a) => a.center?.[0]).filter((x) => Number.isFinite(x))
  if (!xs.length) return { mid: 0, span: 0 }
  const lo = Math.min(...xs)
  const hi = Math.max(...xs)
  return { mid: (lo + hi) / 2, span: hi - lo }
}

// hospitals in the dark areas (assumed on backup power): each area's count, outside the area
export function HospitalsLayer({ report, lang, stage, animate }) {
  const { grid } = useOverload()
  const layer = useMemo(() => {
    const areas = new Map((report?.areas || []).map((a) => [String(a.area).toLowerCase(), a]))
    const rows = (report?.hospitals?.areas || [])
      .map((h) => ({ h, a: areas.get(String(h.area).toLowerCase()) }))
      .filter((x) => x.a?.center && Number(x.h.count) > 0)
      .slice(0, 6)
    const { mid, span } = spanOf(rows.map((x) => x.a))
    const tags = []
    const pts = []
    // every dark area's points (not only those with hospitals): a tag keeps off all of them
    const shapes = [...areas.values()].filter((a) => a?.center).map((a) => ({ a, pts: areaShape({ grid }, a).pts }))
    rows.forEach((x, i) => {
      const others = shapes.filter((s) => s.a !== x.a).flatMap((s) => s.pts)
      const o = outside({ grid }, x.a, mid, span, others)
      if (!o) return
      pts.push(...o.pts, o.room)
      tags.push({ at: o.at, text: P[lang].hospitalsN(Number(x.h.count)), sub: x.a.area, tone: 'lost', side: o.side, delay: animate ? 300 + i * 450 : 0 })
    })
    return tags.length ? { key: 'hospitals', still: !animate, tags, pts } : null
  }, [report, lang, animate, grid])
  const cam = useMemo(() => (layer ? { points: [...layer.tags.map((t) => t.at), ...layer.pts] } : null), [layer])
  useLayer(stage, layer, cam)
  return null
}

// what it would cost, pinned where it lands: the blackout on the dark areas, the upgrades on the lines they raise
export function CostLayer({ report, lang, stage, animate }) {
  const O = useOverload()
  const T = P[lang]
  const { branchById, subPos } = O
  const layer = useMemo(() => {
    const c = report?.cost || {}
    const tags = []
    const hulls = []
    const areas = (report?.areas || []).filter((a) => Number(a.people) > 0 && a.center).slice(0, 3)
    areas.forEach((a) => hulls.push({ key: a.area, pts: areaShape(O, a).pts, tone: 'dim' }))
    const hi = Number(c.blackout_high_usd) || Number(c.ranges?.blackout_usd?.[1]) || 0
    // outside the hardest-hit area, on a short leader (CLEAR AREAS)
    const sp = spanOf(areas)
    const others = areas.slice(1).flatMap((a) => areaShape(O, a).pts)
    const out = hi && areas[0] ? outside(O, areas[0], sp.mid, sp.span, others) : null
    if (out) tags.push({ at: out.at, text: T.costBlackout(moneyShort(hi)), tone: 'lost', side: out.side, delay: animate ? 400 : 0 })
    const best = report?.best_fix != null ? report.fixes?.[report.best_fix] : null
    const first = (best?.detail?.list || [])[0]
    const at = first && branchAt({ branchById, subPos }, first.id)
    if (at && best?.cost?.high) tags.push({ at: at.mid, text: T.costFix(moneyShort(best.cost.high)), tone: 'fix', side: 'nw', delay: animate ? 1200 : 0 })
    const lines = (best?.detail?.list || []).map((l) => ({ id: l.id, tone: 'fix', delay: animate ? 1000 : 0 }))
    return tags.length ? { key: 'cost', still: !animate, tags, hulls, lines } : null
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [report, lang, animate, branchById, subPos])
  const cam = useMemo(() => (layer ? { points: layer.tags.map((t) => t.at) } : null), [layer])
  useLayer(stage, layer, cam)
  return null
}

// no fix: the areas no fix reaches, outlined; the first one says so
export function NoFixLayer({ report, lang, stage, animate }) {
  const O = useOverload()
  const T = P[lang]
  const { grid } = O
  const layer = useMemo(() => {
    const areas = (report?.areas || []).filter((a) => Number(a.people) > 0).slice(0, 6)
    const hulls = areas
      .map((a, i) => ({ key: a.area, pts: areaShape({ grid }, a).pts, tone: 'lost', label: i === 0 ? T.noFixReach : null, delay: animate ? i * 350 : 0 }))
      .filter((h) => h.pts.length)
    return hulls.length ? { key: 'nofix', still: !animate, hulls } : null
  }, [report, grid, animate, T])
  useLayer(stage, layer, { type: 'region' })
  return null
}
