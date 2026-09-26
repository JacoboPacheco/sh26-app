// The unlock curve: how many MORE sites can host a campus of the study's size (y, from zero: today) against
// the money spent on the plan's upgrades, high end of each estimate (x). A step line: nothing changes until
// an upgrade lands. The engine's plan is one series (green: the fix); Gemini's verified bundles are single
// points (blue diamonds) on the same axes, so "does the AI beat the engine for the same money?" reads at a
// glance. The axes follow the build-up: they cover the steps on screen (and every AI bundle), widening as
// the plan grows, so the first cheap steps are readable; the rest of the plan runs on, faint, past the edge.
// Hover snaps to the nearest step; a click picks that budget. Hand-made SVG, sized by viewBox to the panel.
import { useId, useMemo, useRef, useState } from 'react'
import { money } from '../cost/money'
import { fmt } from '../../geo'

const W = 300
const H = 184
const M = { l: 30, r: 14, t: 22, b: 24 }
const IW = W - M.l - M.r
const IH = H - M.t - M.b
const MIN_STEPS = 3 // before the build-up starts, the axes already cover the first steps

function niceMax(v) {
  if (v <= 0) return 1
  const p = 10 ** Math.floor(Math.log10(v))
  for (const k of [1, 1.2, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10]) if (k * p >= v - 1e-9) return k * p
  return 10 * p
}
const short = (x) => {
  if (x >= 1e9) return `$${(x / 1e9).toFixed(1).replace(/\.0$/, '')}B`
  if (x >= 1e6) return `$${Math.round(x / 1e6)}M`
  return x ? `$${Math.round(x / 1e3)}k` : '$0'
}

export default function UnlockChart({ result, shown, bundle, onPick, onBundle }) {
  const wrap = useRef(null)
  const clip = useId().replace(/:/g, '')
  const [hover, setHover] = useState(null) // {kind: 'step', i} | {kind: 'bundle', i}
  const steps = result.steps
  const bundles = useMemo(() => result.ai?.bundles || [], [result])
  const ok0 = result.before.sites_ok
  const n = Math.min(shown, steps.length)
  const reach = Math.max(n, Math.min(MIN_STEPS, steps.length))

  const pts = useMemo(() => [{ c: 0, m: 0 }, ...steps.map((s) => ({ c: s.cum_cost.high, m: s.sites_ok - ok0 }))], [steps, ok0])
  const geo = useMemo(() => {
    const maxCost = niceMax(Math.max(pts[reach].c, ...bundles.map((b) => b.cost.high), 1) * 1.08)
    const maxMore = niceMax(Math.max(pts[reach].m, ...bundles.map((b) => b.more_sites), 1) * 1.12)
    const x = (c) => M.l + (c / maxCost) * IW
    const y = (m) => M.t + IH - (m / maxMore) * IH
    const path = (upto) => {
      let d = `M${x(0)},${y(0)}`
      for (let i = 1; i <= upto; i++) d += `H${x(pts[i].c).toFixed(1)}V${y(pts[i].m).toFixed(1)}`
      return d
    }
    const xt = [0, 0.5, 1].map((f) => f * maxCost)
    const yt = [0, 0.5, 1].map((f) => Math.round(f * maxMore)).filter((t, i, a) => a.indexOf(t) === i)
    return { x, y, path, xt, yt, maxCost }
  }, [pts, reach, bundles])

  const end = pts[n]
  const nearestStep = (px) => {
    let best = 0
    let d = Infinity
    pts.forEach((p, i) => {
      if (p.c > geo.maxCost) return
      const dd = Math.abs(geo.x(p.c) - px)
      if (dd < d) {
        d = dd
        best = i
      }
    })
    return best
  }
  const localX = (e) => {
    const r = wrap.current.getBoundingClientRect()
    return ((e.clientX - r.left) / r.width) * W
  }
  const tip = hover
    ? hover.kind === 'bundle'
      ? {
          b: bundles[hover.i],
          x: geo.x(bundles[hover.i].cost.high),
          y: geo.y(bundles[hover.i].more_sites),
        }
      : { s: hover.i, x: geo.x(pts[hover.i].c), y: geo.y(pts[hover.i].m) }
    : null

  return (
    <div className="ul-chart" ref={wrap}>
      <svg
        viewBox={`0 0 ${W} ${H}`}
        role="img"
        aria-label={`More sites that can host ${fmt(result.mw)} MW: none today (${ok0} can already), ${end.m} more after ${n} of ${steps.length} plan steps costing ${money(end.c)} (high end).`}
        onPointerMove={(e) => setHover({ kind: 'step', i: nearestStep(localX(e)) })}
        onPointerLeave={() => setHover(null)}
        onClick={(e) => onPick(nearestStep(localX(e)))}
      >
        {/* recessive frame: hairline gridlines at the y ticks, x ticks under the axis */}
        {geo.yt.map((t) => (
          <g key={`y${t}`}>
            <line className="ul-grid" x1={M.l} x2={W - M.r} y1={geo.y(t)} y2={geo.y(t)} />
            <text className="ul-tick" x={M.l - 5} y={geo.y(t) + 3.5} textAnchor="end">
              {fmt(t)}
            </text>
          </g>
        ))}
        {geo.xt.map((t, i) => (
          <text key={`x${i}`} className="ul-tick" x={geo.x(t)} y={H - 8} textAnchor={i === 0 ? 'start' : i === geo.xt.length - 1 ? 'end' : 'middle'}>
            {short(t)}
          </text>
        ))}
        <defs>
          <clipPath id={clip}>
            <rect x={M.l - 6} y={M.t - 8} width={IW + 12} height={IH + 14} />
          </clipPath>
        </defs>
        <text className="ul-note" x={M.l - 26} y={10}>
          More sites that can host {fmt(result.mw)} MW (today {fmt(ok0)})
        </text>
        {/* the whole plan, faint (it runs on past the edge); the built part, green */}
        <g clipPath={`url(#${clip})`}>
          <path className="ul-curve ul-curve--ghost" d={geo.path(steps.length)} />
          <path className={`ul-curve${bundle != null ? ' ul-curve--dim' : ''}`} d={geo.path(n)} />
        </g>
        {tip?.s != null && <line className="ul-cross" x1={tip.x} x2={tip.x} y1={M.t} y2={M.t + IH} />}
        {/* Gemini's bundles: a diamond each, the engine re-ran every one */}
        {bundles.map((b, i) => {
          const cx = geo.x(b.cost.high)
          const cy = geo.y(b.more_sites)
          return (
            <g
              key={b.name + i}
              className={`ul-diamond${bundle === i ? ' ul-diamond--on' : ''}`}
              transform={`translate(${cx} ${cy})`}
              onPointerMove={(e) => {
                e.stopPropagation()
                setHover({ kind: 'bundle', i })
              }}
              onClick={(e) => {
                e.stopPropagation()
                onBundle(i)
              }}
            >
              <circle className="ul-hit" r={12} />
              <rect x={-4.5} y={-4.5} width={9} height={9} transform="rotate(45)" />
            </g>
          )
        })}
        <circle className="ul-end" cx={geo.x(end.c)} cy={geo.y(end.m)} r={4.5} />
        {n > 0 && (
          <text className="ul-note ul-note--end" x={geo.x(end.c) - 7} y={geo.y(end.m) - 7} textAnchor="end">
            +{fmt(end.m)}
          </text>
        )}
      </svg>
      {tip && (
        <div
          className="ul-tip"
          style={{
            left: `${(tip.x / W) * 100}%`,
            top: `${(tip.y / H) * 100}%`,
          }}
          role="status"
        >
          {tip.b ? (
            <>
              <strong>+{fmt(tip.b.more_sites)} sites</strong>
              <span>{money(tip.b.cost.high)}</span>
              <span className="ul-tip__who">Gemini: {tip.b.name}</span>
            </>
          ) : (
            <>
              <strong>+{fmt(pts[tip.s].m)} sites</strong>
              <span>
                {money(pts[tip.s].c)} · {fmt(ok0 + pts[tip.s].m)} in all
              </span>
              <span className="ul-tip__who">{tip.s ? `Engine plan, step ${tip.s}` : 'Today, no upgrades'}</span>
            </>
          )}
        </div>
      )}
      <div className="ul-axis-x">Money spent on upgrades (high end)</div>
      {bundles.length > 0 && (
        <ul className="ul-legend" aria-label="Legend">
          <li>
            <span className="ul-key ul-key--line" aria-hidden="true" />
            Engine plan
          </li>
          <li>
            <span className="ul-key ul-key--diamond" aria-hidden="true" />
            Gemini bundle
          </li>
        </ul>
      )}
    </div>
  )
}
