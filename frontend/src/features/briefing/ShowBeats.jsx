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

// hospitals on backup power: a pin on each area with its count
export function HospitalsLayer({ report, lang, stage, animate }) {
  const layer = useMemo(() => {
    const areas = new Map((report?.areas || []).map((a) => [String(a.area).toLowerCase(), a]))
    const tags = (report?.hospitals?.areas || [])
      .map((h) => ({ h, a: areas.get(String(h.area).toLowerCase()) }))
      .filter((x) => x.a?.center && Number(x.h.count) > 0)
      .slice(0, 6)
      .map((x, i) => ({ at: x.a.center, text: P[lang].hospitalsN(Number(x.h.count)), tone: 'lost', side: ['sw', 'nw', 'se', 'ne'][i % 4], delay: animate ? 300 + i * 450 : 0 }))
    return tags.length ? { key: 'hospitals', still: !animate, tags } : null
  }, [report, lang, animate])
  const cam = useMemo(() => (layer ? { points: layer.tags.map((t) => t.at) } : null), [layer])
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
    // the app labels each dark town to the right of it: these pins go to the left
    if (hi && areas[0]) tags.push({ at: areas[0].center, text: T.costBlackout(moneyShort(hi)), tone: 'lost', side: 'sw', delay: animate ? 400 : 0 })
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
