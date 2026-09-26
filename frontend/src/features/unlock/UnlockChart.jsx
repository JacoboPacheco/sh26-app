// The unlock curve on the Strengthen page: GW of site options (y: more sites that can host the campus, times its
// size, each site tested alone, never capacity that connects together) against the money spent on the plan's
// upgrades (x, high end of each estimate, a log scale: the plan's cost spans three decades and its first, cheapest
// steps matter most). A step line: nothing changes until an upgrade lands.
//  - the whole plan runs faint to its end; the part the budget buys is green, the part built so far (the build-up)
//    solid; the budget is a vertical rule with its amount;
//  - Gemini's verified bundles are blue diamonds on the same axes ("does the AI beat the engine for the money?");
//  - hover snaps to the nearest step (crosshair + tooltip); a click sets the budget to that step.
// Hand-made SVG drawn at its column's own width (a ResizeObserver), so its text stays at its real size.
import { useId, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { fmt } from '../../geo'
import { money } from '../cost/money'
import { shortMoney, siteOptions } from './budget'

const H = 164
const M = { l: 44, r: 14, t: 22, b: 22 }
const IH = H - M.t - M.b

function niceMax(v) {
  if (v <= 0) return 1
  const p = 10 ** Math.floor(Math.log10(v))
  for (const k of [1, 1.2, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10]) if (k * p >= v - 1e-9) return k * p
  return 10 * p
}
const gwText = (gw) => `${gw.toLocaleString('en-US', { maximumFractionDigits: gw < 10 ? 1 : 0 })} GW`

export default function UnlockChart({ result, shown, target, budget, bundle, onBudget, onBundle }) {
  const wrap = useRef(null)
  const [W, setW] = useState(480)
  useLayoutEffect(() => {
    const el = wrap.current
    if (!el || typeof ResizeObserver === 'undefined') return undefined
    const ro = new ResizeObserver(([e]) => setW(Math.max(260, Math.round(e.contentRect.width))))
    ro.observe(el)
    return () => ro.disconnect()
  }, [])
  const IW = W - M.l - M.r
  const clip = useId().replace(/:/g, '')
  const [hover, setHover] = useState(null) // {kind: 'step', i} | {kind: 'bundle', i}
  const steps = result.steps
  const bundles = useMemo(() => result.ai?.bundles || [], [result])
  const ok0 = result.before.sites_ok
  const size = result.mw
  const n = Math.min(shown, steps.length)

  const pts = useMemo(
    () => [
      { c: 0, gw: 0 },
      ...steps.map((s) => ({
        c: s.cum_cost.high,
        gw: ((s.sites_ok - ok0) * size) / 1000,
      })),
    ],
    [steps, ok0, size],
  )
  const geo = useMemo(() => {
    const all = pts.at(-1).c || 1
    const lo = 10 ** Math.floor(Math.log10(Math.max((steps[0]?.cum_cost.high || all) / 2, 1e5)))
    const hi = Math.max(all, ...bundles.map((b) => b.cost.high)) * 1.15
    const a = Math.log10(lo)
    const b = Math.log10(hi)
    const x = (c) => M.l + ((Math.log10(Math.max(c, lo)) - a) / (b - a)) * IW
    const maxGw = niceMax(Math.max(pts.at(-1).gw, ...bundles.map((bb) => (bb.more_sites * size) / 1000), 0.5) * 1.08)
    const y = (gw) => M.t + IH - (gw / maxGw) * IH
    const path = (from, upto) => {
      let d = `M${x(pts[from].c).toFixed(1)},${y(pts[from].gw).toFixed(1)}`
      for (let i = from + 1; i <= upto; i++) d += `H${x(pts[i].c).toFixed(1)}V${y(pts[i].gw).toFixed(1)}`
      return d
    }
    const xt = []
    for (let e = Math.ceil(a); e <= Math.floor(b); e++) xt.push(10 ** e)
    const yt = [0, maxGw / 2, maxGw]
    return { x, y, path, xt, yt, lo }
  }, [pts, steps, bundles, size, IW])

  const t = Math.min(target, steps.length)
  const end = pts[n]
  const nearestStep = (px) => {
    let best = 0
    let d = Infinity
    pts.forEach((p, i) => {
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
          y: geo.y((bundles[hover.i].more_sites * size) / 1000),
        }
      : { s: hover.i, x: geo.x(pts[hover.i].c), y: geo.y(pts[hover.i].gw) }
    : null
  const bx = geo.x(Math.max(budget, geo.lo))

  return (
    <figure className="st-chart" ref={wrap}>
      <figcaption className="st-chart__cap">
        <strong>GW of site options</strong> (each site tested alone) against money spent, log scale
      </figcaption>
      <div className="st-chart__plot">
        <svg
          viewBox={`0 0 ${W} ${H}`}
          width={W}
          height={H}
          role="img"
          aria-label={`GW of site options against money spent: ${gwText(end.gw)} after ${fmt(n)} plan steps costing ${money(end.c)}; the whole plan reaches ${gwText(pts.at(-1).gw)} at ${money(pts.at(-1).c)}.`}
          onPointerMove={(e) => setHover({ kind: 'step', i: nearestStep(localX(e)) })}
          onPointerLeave={() => setHover(null)}
          onClick={(e) => {
            const i = nearestStep(localX(e))
            onBudget(pts[i].c)
          }}
        >
          <defs>
            <clipPath id={clip}>
              <rect x={M.l - 6} y={M.t - 10} width={IW + 12} height={IH + 16} />
            </clipPath>
          </defs>
          {/* the budget's share of the axis, very faint */}
          <rect className="st-chart__spent" x={M.l} y={M.t} width={Math.max(0, bx - M.l)} height={IH} />
          {geo.yt.map((v) => (
            <g key={`y${v}`}>
              <line className="ul-grid" x1={M.l} x2={W - M.r} y1={geo.y(v)} y2={geo.y(v)} />
              <text className="ul-tick" x={M.l - 6} y={geo.y(v) + 3.5} textAnchor="end">
                {v ? gwText(v) : '0'}
              </text>
            </g>
          ))}
          {geo.xt.map((v) => (
            <text key={`x${v}`} className="ul-tick" x={geo.x(v)} y={H - 8} textAnchor="middle">
              {shortMoney(v)}
            </text>
          ))}
          <g clipPath={`url(#${clip})`}>
            <path className="ul-curve ul-curve--ghost" d={geo.path(0, pts.length - 1)} />
            {t > 0 && <path className="st-curve--budget" d={geo.path(0, t)} />}
            {n > 0 && <path className={`ul-curve${bundle != null ? ' ul-curve--dim' : ''}`} d={geo.path(0, n)} />}
          </g>
          {/* the budget: a rule and its amount */}
          <line className="st-chart__budget" x1={bx} x2={bx} y1={M.t - 6} y2={M.t + IH} />
          <text className="st-chart__blabel" x={bx + (bx > W - 120 ? -5 : 5)} y={M.t - 12} textAnchor={bx > W - 120 ? 'end' : 'start'}>
            Budget {shortMoney(budget)}
          </text>
          {tip?.s != null && <line className="ul-cross" x1={tip.x} x2={tip.x} y1={M.t} y2={M.t + IH} />}
          {bundles.map((b, i) => (
            <g
              key={b.name + i}
              className={`ul-diamond${bundle === i ? ' ul-diamond--on' : ''}`}
              transform={`translate(${geo.x(b.cost.high)} ${geo.y((b.more_sites * size) / 1000)})`}
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
          ))}
          {n > 0 && (
            <>
              <circle className="ul-end" cx={geo.x(end.c)} cy={geo.y(end.gw)} r={4.5} />
              <text className="ul-note ul-note--end" x={geo.x(end.c) - 7} y={geo.y(end.gw) - 8} textAnchor="end">
                +{gwText(end.gw)}
              </text>
            </>
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
                <strong>
                  +{fmt(tip.b.more_sites)} {tip.b.more_sites === 1 ? 'site' : 'sites'} · {siteOptions(tip.b.more_sites * size)} of site options
                </strong>
                <span>{money(tip.b.cost.high)} (high end)</span>
                <span className="ul-tip__who">Gemini: {tip.b.name}, re-run by the engine</span>
              </>
            ) : (
              <>
                <strong>
                  +{fmt((pts[tip.s].gw * 1000) / size)} sites
                  {pts[tip.s].gw ? ` · ${siteOptions(pts[tip.s].gw * 1000)} of site options` : ''}
                </strong>
                <span>
                  {money(pts[tip.s].c)} · {fmt(ok0 + (pts[tip.s].gw * 1000) / size)} sites in all
                </span>
                <span className="ul-tip__who">{tip.s ? `Engine plan, step ${tip.s}. Click to set the budget here.` : 'Today, no upgrades'}</span>
              </>
            )}
          </div>
        )}
      </div>
      <div className="st-chart__foot">
        <ul className="ul-legend" aria-label="Legend">
          <li>
            <span className="ul-key ul-key--line" aria-hidden="true" />
            Engine plan
          </li>
          {bundles.length > 0 && (
            <li>
              <span className="ul-key ul-key--diamond" aria-hidden="true" />
              Gemini bundle, engine-verified
            </li>
          )}
        </ul>
        <p className="st-chart__whole">
          Whole plan: +{gwText(pts.at(-1).gw)} for {shortMoney(pts.at(-1).c)}
        </p>
      </div>
    </figure>
  )
}
