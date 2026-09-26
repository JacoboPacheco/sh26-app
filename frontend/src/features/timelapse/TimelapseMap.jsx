// The time-lapse's own map: the state's synthetic grid drawn faint, the lines the campuses load up warming from quiet
// to amber to red as the years go by (a CSS transition carries each change across most of the year's 1.5 s), and the
// reported campuses as rings that draw themselves in the year they arrive. Rendering only: every number is the backend's.
import { useMemo } from 'react'
import outline from '../../data/florida_outline.json'
import { HEIGHT, STATES, WIDTH, citiesFor, project, toPath } from '../../geo'
import { mwText, tlClass } from './timelapseApi'

const LABELS_MAX = 3
const RING_MIN = 4.5
const RING_PER_SQRT_MW = 0.42 // map units per sqrt(MW): 1 GW ~ 13 units, 10 GW ~ 42

const CH = 5.7 // map units per character of a 10 px bold label (a rough width, enough to keep labels apart)
const CH_SUB = 5.1 // ... of its lighter second line

const ringR = (mw) => Math.max(RING_MIN, Math.sqrt(Math.max(mw, 0)) * RING_PER_SQRT_MW)
const onlineBy = (c, year) => (year == null ? 0 : c.arrivals.filter((a) => a.year <= year).reduce((s, a) => s + a.mw, 0))
const mwIn = (c, year) => c.arrivals.filter((a) => a.year === year).reduce((s, a) => s + a.mw, 0)
const overlaps = (a, b) => a[0] < b[2] && b[0] < a[2] && a[1] < b[3] && b[1] < a[3] // boxes [x0, y0, x1, y1]

export default function TimelapseMap({ grid, region, data, frame, pcts, prevPcts, newIds }) {
  const subs = useMemo(() => new Map((grid?.subs || []).map((s) => [s.id, s])), [grid])
  const branches = useMemo(() => new Map((grid?.branches || []).map((b) => [b.id, b])), [grid])

  // the whole network, faint, as one path (drawn once)
  const network = useMemo(() => {
    let d = ''
    for (const b of grid?.branches || []) {
      if (b.from_sub === b.to_sub) continue
      const a = subs.get(b.from_sub)
      const z = subs.get(b.to_sub)
      if (!a || !z) continue
      const [x1, y1] = project(a.lon, a.lat)
      const [x2, y2] = project(z.lon, z.lat)
      d += `M${x1.toFixed(1)} ${y1.toFixed(1)}L${x2.toFixed(1)} ${y2.toFixed(1)}`
    }
    return d
  }, [grid, subs])

  const land = useMemo(() => {
    if (region === 'FL') return { d: outline.land.map((r) => toPath(r)).join(''), lakes: outline.lakes.map((l) => toPath(l)) }
    return { d: (STATES[region]?.rings || []).map((r) => toPath(r)).join(''), lakes: [] }
  }, [region])

  // the warm set: geometry once per payload, the loading per frame
  const warm = useMemo(() => {
    return (data?.lines?.ids || []).map((id, k) => {
      const b = branches.get(id)
      if (!b) return null
      const a = subs.get(b.from_sub)
      const z = subs.get(b.to_sub)
      if (!a || !z) return null
      const p1 = project(a.lon, a.lat)
      const p2 = project(z.lon, z.lat)
      return { id, k, xf: b.from_sub === b.to_sub, p1, p2 }
    }).filter(Boolean)
  }, [data, branches, subs])

  const cities = useMemo(() => citiesFor(grid, 8), [grid])
  const year = frame?.year ?? null
  const campuses = useMemo(() => data?.campuses || [], [data])
  const top = frame?.busiest?.[0]

  // Name the year's biggest arrivals (the readout lists every one) and the busiest element once it is over its rating,
  // each on a side with room: a label that would overlap one already placed flips sides, else stays unnamed on the map.
  const layout = useMemo(() => {
    const boxes = []
    const fits = (b) => b[0] >= 0 && b[2] <= WIDTH && !boxes.some((o) => overlaps(o, b))
    const labels = new Map()
    const arriving = campuses
      .filter((c) => newIds.has(c.id))
      .sort((a, b) => mwIn(b, year) - mwIn(a, year))
      .slice(0, LABELS_MAX)
    for (const c of arriving) {
      const [x, y] = project(c.lon, c.lat)
      const r = ringR(onlineBy(c, year))
      const sub = `${mwText(mwIn(c, year))} · ${c.status === 'operating' && year === 2026 ? 'operating, as reported' : `reported for ${year}`}`
      const w = Math.max(c.name.length * CH, sub.length * CH_SUB)
      const pref = x > WIDTH * 0.62 ? -1 : 1
      for (const s of [pref, -pref]) {
        const lx = x + s * (r + 5)
        const box = [s > 0 ? lx : lx - w, y - 11, s > 0 ? lx + w : lx, y + 13]
        if (fits(box)) {
          boxes.push(box)
          labels.set(c.id, { lx, left: s < 0, sub })
          break
        }
      }
    }
    let callout = null
    if (top && top.pct >= 100 && top.mid) {
      const [x, y] = project(top.mid[1], top.mid[0])
      const w = Math.max(`${Math.round(top.pct)} % of its rating`.length * CH, `the busiest ${top.kind}, ${top.kv} kV`.length * CH_SUB)
      const pref = x > WIDTH * 0.62 ? -1 : 1
      for (const [s, dy] of [[pref, 22], [-pref, 22], [pref, -34], [-pref, -34]]) {
        const ly = y + dy
        const tx = x + s * 19
        const box = [s > 0 ? tx : tx - w, ly - 7, s > 0 ? tx + w : tx, ly + 17]
        if (fits(box)) {
          callout = { x, y, s, ly, lx: x + s * 16, tx }
          break
        }
      }
    }
    return { labels, callout }
  }, [campuses, newIds, year, top])
  const { labels, callout } = layout

  // every element over its rating gets a mark at its middle; one that went over THIS year sends out one ring (a ping),
  // so the year's new overloads read on the map, not only in the charts
  const overMarks = useMemo(
    () =>
      warm
        .filter((w) => (pcts?.[w.k] ?? 0) >= 100)
        .map((w) => ({
          id: w.id,
          x: (w.p1[0] + w.p2[0]) / 2,
          y: (w.p1[1] + w.p2[1]) / 2,
          fresh: !!prevPcts && (prevPcts[w.k] ?? 0) < 100,
        })),
    [warm, pcts, prevPcts],
  )

  return (
    <svg className="tl-map" viewBox={`0 0 ${WIDTH.toFixed(1)} ${HEIGHT.toFixed(1)}`} role="img" aria-label={`Synthetic grid model of ${data?.region_name || 'the state'}: lines by loading${year ? ` in ${year}` : ''}, reported campuses as rings`}>
      <path className="map-land tl-land" d={land.d} />
      {land.lakes.map((d, i) => (
        <path key={i} className="tl-lake" d={d} />
      ))}
      <path className="tl-network" d={network} />
      {cities.map((c) => {
        const [x, y] = project(c.lon, c.lat)
        return (
          <text key={c.name} className="tl-city" x={x + 5} y={y + 3}>
            {c.name}
          </text>
        )
      })}
      <g className="tl-warm">
        {warm.map((w) => {
          const pct = pcts?.[w.k] ?? 0
          const cls = tlClass(pct)
          if (w.xf) {
            if (pct < 80) return null
            return <circle key={w.id} className={`${cls} tl-xf`} cx={w.p1[0]} cy={w.p1[1]} r={pct >= 100 ? 3.2 : 2.4} />
          }
          return <line key={w.id} className={cls} x1={w.p1[0]} y1={w.p1[1]} x2={w.p2[0]} y2={w.p2[1]} />
        })}
      </g>
      <g className="tl-overmarks" aria-hidden="true">
        {overMarks.map((m) => (
          <g key={m.id}>
            <circle className="tl-overmark" cx={m.x} cy={m.y} r={2.2} />
            {m.fresh && <circle key={`ping-${year}`} className="tl-ping" cx={m.x} cy={m.y} r={3} />}
          </g>
        ))}
      </g>
      {callout && (
        <g key={`callout-${year}`} className="tl-callout" aria-hidden="true">
          <polyline points={`${callout.x},${callout.y} ${callout.x + callout.s * 8},${callout.ly} ${callout.lx},${callout.ly}`} />
          <text x={callout.tx} y={callout.ly + 3.5} textAnchor={callout.s < 0 ? 'end' : 'start'}>
            <tspan className="tl-callout__pct">{Math.round(top.pct)} %</tspan>
            <tspan> of its rating</tspan>
            <tspan x={callout.tx} dy="1.2em" className="tl-callout__sub">
              the busiest {top.kind}, {top.kv} kV
            </tspan>
          </text>
        </g>
      )}
      <g className="tl-rings">
        {campuses.map((c) => {
          const online = onlineBy(c, year)
          if (online <= 0) return null
          const [x, y] = project(c.lon, c.lat)
          const r = ringR(online)
          const fresh = newIds.has(c.id)
          const circ = 2 * Math.PI * r
          const label = labels.get(c.id)
          return (
            <g key={c.id} className={`tl-ring${fresh ? ' tl-ring--new' : ''}`}>
              <circle cx={x} cy={y} r={r} style={{ '--circ': circ.toFixed(1) }} strokeDasharray={circ.toFixed(1)}>
                <title>{`${c.name}: ${mwText(online)} online on the model by ${year} (reported size ${mwText(c.mw)}, ${c.status}, as reported${c.higher_end ? '; full size from its first reported year, the higher end' : ''})`}</title>
              </circle>
              <circle className="tl-ring__dot" cx={x} cy={y} r={1.6} />
              {label && (
                <text className="tl-ring__label" x={label.lx} y={y - 2} textAnchor={label.left ? 'end' : 'start'}>
                  <tspan>{c.name}</tspan>
                  <tspan x={label.lx} dy="1.25em" className="tl-ring__mw">
                    {label.sub}
                  </tspan>
                </text>
              )}
            </g>
          )
        })}
      </g>
    </svg>
  )
}
