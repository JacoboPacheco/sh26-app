import { useId, useLayoutEffect, useRef, useState } from 'react'
import { fmt } from '../../geo'
import { useOverload } from '../../store'
import { EmptyState, ErrorBanner, Loading } from '../../ui'
import { compact, forecastBody, sweepBody, useForecast, useSweep } from './forecastApi'
import './forecast.css'

// Does size matter here? People without power (estimate) at this site for a campus of 100-2,000 MW,
// flexible and firm, from /api/forecast/sweep. The shaded zone is the site's room (nothing overloads);
// the vertical line is your size. Hover or arrow keys read each size; click or Enter tries it.
export default function SweepChart() {
  const o = useOverload()
  const body = sweepBody(o)
  const s = useSweep(body)
  const f = useForecast(forecastBody(o)) // the exact numbers at your size (shared with ForecastCard)
  const firm = !!o?.firm

  if (!body) {
    return (
      <EmptyState title="No site yet">Drop a data center to see how its size changes who loses power.</EmptyState>
    )
  }
  if (s.error) return <ErrorBanner error={s.error} onRetry={s.retry} />
  const data = s.shown
  // the sweep answers progressively; a curve needs two sizes
  if (!data || data.sizes.length < 2) {
    return <Loading label={`Testing 20 sizes at this site…${data && !s.stale ? ` ${data.tested} of ${data.total}` : ''}`} />
  }

  const testing = data.complete === false
  // the dots at your exact size, when the forecast for it is in
  const now = f.data && !f.stale && f.data.main_mw === o.mw ? f.data.cascade : null
  return (
    <figure className={`sc${s.stale ? ' sc--stale' : ''}`} aria-busy={s.stale || testing || undefined}>
      <figcaption className="sc__cap">
        <span className="sc__title">People without power by campus size (estimate)</span>
        <span className="sc__sub">
          {data.sub_area ? `${data.sub_area}, ` : ''}
          {fmt(data.min_mw)}–{fmt(data.max_mw)} MW in {fmt(data.step_mw)} MW steps, on a synthetic grid model
          {s.stale ? (
            <span className="sc__updating"> · updating…</span>
          ) : (
            testing && (
              <span className="sc__updating">
                {' '}
                · testing sizes, {data.tested} of {data.total}…
              </span>
            )
          )}
        </span>
      </figcaption>
      <Legend firm={firm} />
      <Plot data={data} mw={o.mw} firm={firm} now={now} onPick={(v) => o.setMw?.(v)} />
      {data.shape_sentence && <p className="sc__shape">{data.shape_sentence}</p>}
      <Table data={data} />
    </figure>
  )
}

function Legend({ firm }) {
  return (
    <ul className="sc-legend" aria-label="Lines">
      <li className={firm ? 'sc-legend__dim' : undefined}>
        <span className="sc-key sc-key--flex" aria-hidden="true" />
        Flexible{!firm && <span className="sc-legend__tag">your setting</span>}
      </li>
      <li className={firm ? undefined : 'sc-legend__dim'}>
        <span className="sc-key sc-key--firm" aria-hidden="true" />
        Firm{firm && <span className="sc-legend__tag">your setting</span>}
      </li>
    </ul>
  )
}

const H = 206
const M = { l: 40, r: 14, t: 34, b: 28 } // two label rows on top: the room, then your size

function Plot({ data, mw, firm, now, onPick }) {
  const wrap = useRef(null)
  const [w, setW] = useState(320)
  const [hi, setHi] = useState(null) // the hovered / focused size's index
  const liveId = useId()
  useLayoutEffect(() => {
    const el = wrap.current
    if (!el) return undefined
    const measure = () => setW(Math.max(220, Math.round(el.getBoundingClientRect().width)))
    measure()
    const ro = new ResizeObserver(measure)
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  const rows = data.sizes
  const xMax = data.max_mw
  const x = (v) => M.l + (Math.min(Math.max(v, 0), xMax) / xMax) * (w - M.l - M.r)
  const top = Math.max(1000, ...rows.map((r) => Math.max(r.people_flexible, r.people_firm)), now?.flexible?.people || 0, now?.firm?.people || 0)
  const { max: yMax, ticks } = niceTicks(top)
  const y = (v) => M.t + (1 - v / yMax) * (H - M.t - M.b)
  const path = (key) => rows.map((r, i) => `${i ? 'L' : 'M'}${x(r.mw).toFixed(1)},${y(r[key]).toFixed(1)}`).join('')
  const xTicks = (w < 340 ? [0, 1000, 2000] : [0, 500, 1000, 1500, 2000]).filter((t) => t <= xMax)

  const room = !data.room_capped && data.room_mw != null && data.room_mw <= xMax ? data.room_mw : null
  const showMw = mw != null && mw <= xMax ? mw : null
  const nearest = (v) => rows.reduce((b, r, i) => (Math.abs(r.mw - v) < Math.abs(rows[b].mw - v) ? i : b), 0)

  const fromPointer = (e) => {
    const box = e.currentTarget.getBoundingClientRect()
    const px = ((e.clientX - box.left) / box.width) * w
    const v = ((px - M.l) / (w - M.l - M.r)) * xMax
    return nearest(v)
  }
  const onKey = (e) => {
    const cur = hi ?? nearest(mw ?? rows[0].mw)
    let next = null
    if (e.key === 'ArrowRight' || e.key === 'ArrowUp') next = Math.min(rows.length - 1, cur + 1)
    else if (e.key === 'ArrowLeft' || e.key === 'ArrowDown') next = Math.max(0, cur - 1)
    else if (e.key === 'Home') next = 0
    else if (e.key === 'End') next = rows.length - 1
    else if ((e.key === 'Enter' || e.key === ' ') && hi != null) {
      e.preventDefault()
      onPick(rows[hi].mw)
      return
    } else if (e.key === 'Escape') {
      setHi(null)
      return
    }
    if (next != null) {
      e.preventDefault()
      setHi(next)
    }
  }

  const r = hi != null ? rows[hi] : null
  // labels on top, one row each (the room, then your size), beside their lines; flipped at the edges
  const mwRight = showMw == null || room == null || showMw >= room
  const label = (at, text, right, row) => {
    const est = text.length * 6.1
    let anchor = right ? 'start' : 'end'
    let lx = right ? at + 5 : at - 5
    if (right && lx + est > w - 2) [anchor, lx] = ['end', at - 5]
    if (!right && lx - est < 2) [anchor, lx] = ['start', at + 5]
    return (
      <text x={lx} y={row === 0 ? 11 : 25} textAnchor={anchor} className="sc-svg__label">
        {text}
      </text>
    )
  }

  return (
    <div
      ref={wrap}
      className="sc-plot"
      tabIndex={0}
      role="group"
      aria-label="Campus size chart. Use the left and right arrow keys to read each size, and Enter to try it."
      aria-describedby={liveId}
      onKeyDown={onKey}
      onBlur={() => setHi(null)}
    >
      <svg
        width={w}
        height={H}
        viewBox={`0 0 ${w} ${H}`}
        className="sc-svg"
        role="img"
        aria-label={`People without power at ${fmt(rows[0].mw)} to ${fmt(xMax)} MW. ${data.shape_sentence || 'Sizes are still being tested.'}`}
        onPointerMove={(e) => setHi(fromPointer(e))}
        onPointerLeave={() => setHi(null)}
        onClick={(e) => onPick(rows[fromPointer(e)].mw)}
      >
        {room != null && (
          <g className="sc-svg__room">
            <rect x={M.l} y={M.t} width={Math.max(0, x(room) - M.l)} height={H - M.t - M.b} />
            <line x1={x(room)} x2={x(room)} y1={16} y2={H - M.b} />
            {label(x(room), `Room ${fmt(room)} MW`, !mwRight, 0)}
          </g>
        )}
        {ticks.map((t) => (
          <g key={t} className="sc-svg__grid">
            <line x1={M.l} x2={w - M.r} y1={y(t)} y2={y(t)} />
            <text x={M.l - 6} y={y(t) + 3.5} textAnchor="end">
              {compact(t)}
            </text>
          </g>
        ))}
        {xTicks.map((t) => (
          <text key={t} x={x(t)} y={H - M.b + 16} textAnchor={t === 0 ? 'start' : t === xMax ? 'end' : 'middle'} className="sc-svg__tick">
            {t === xMax ? `${fmt(t)} MW` : fmt(t)}
          </text>
        ))}
        <path d={path('people_flexible')} className={`sc-svg__line sc-svg__line--flex${firm ? ' sc-svg__line--dim' : ''}`} />
        <path d={path('people_firm')} className={`sc-svg__line sc-svg__line--firm${firm ? '' : ' sc-svg__line--dim'}`} />
        {showMw != null && (
          <g className="sc-svg__now">
            <line x1={x(showMw)} x2={x(showMw)} y1={30} y2={H - M.b} />
            {label(x(showMw), `You: ${fmt(showMw)} MW`, mwRight, 1)}
            {now?.flexible && <circle cx={x(showMw)} cy={y(now.flexible.people)} r={4.5} className="sc-dot sc-dot--flex" />}
            {now?.firm && <circle cx={x(showMw)} cy={y(now.firm.people)} r={4.5} className="sc-dot sc-dot--firm" />}
          </g>
        )}
        {r && (
          <g className="sc-svg__hover" aria-hidden="true">
            <line x1={x(r.mw)} x2={x(r.mw)} y1={M.t} y2={H - M.b} />
            <circle cx={x(r.mw)} cy={y(r.people_flexible)} r={4} className="sc-dot sc-dot--flex" />
            <circle cx={x(r.mw)} cy={y(r.people_firm)} r={4} className="sc-dot sc-dot--firm" />
          </g>
        )}
        <line x1={M.l} x2={w - M.r} y1={H - M.b} y2={H - M.b} className="sc-svg__axis" />
      </svg>
      {r && <Tip r={r} left={x(r.mw)} w={w} />}
      <p id={liveId} className="sc-live" aria-live="polite">
        {r ? describe(r) : ''}
      </p>
    </div>
  )
}

function Tip({ r, left, w }) {
  const width = Math.min(236, w)
  // beside the hover line, never over it, so the dots it describes stay visible (right when it
  // fits, else left; a chart narrower than two tips falls back to the nearer edge)
  const gap = 12
  let l = left + gap + width <= w ? left + gap : left - gap - width
  l = Math.min(Math.max(l, 0), Math.max(0, w - width))
  return (
    <div className="sc-tip" style={{ left: l, width }} aria-hidden="true">
      <strong className="sc-tip__h">{fmt(r.mw)} MW</strong>
      <span className="sc-tip__row">
        <span className="sc-key sc-key--flex" />
        <b>{fmt(r.people_flexible)}</b> flexible
      </span>
      <span className="sc-tip__sub">
        {plural(r.steps_flexible, 'step')}
        {r.cut_off ? ', campus cut off' : ''}
      </span>
      <span className="sc-tip__row">
        <span className="sc-key sc-key--firm" />
        <b>{fmt(r.people_firm)}</b> firm
      </span>
      <span className="sc-tip__sub">
        {plural(r.steps_firm, 'step')}
        {r.people_firm > 0 ? (r.firm_held === false ? ', cut off anyway' : `, ${fmt(r.shed_mw)} MW of others cut`) : ''}
      </span>
      <span className="sc-tip__foot">
        {r.over ? `${plural(r.over, 'line')} over limit` : 'No line over limit'}. Click to try this size.
      </span>
    </div>
  )
}

function Table({ data }) {
  return (
    <details className="sc-table">
      <summary>Show the numbers</summary>
      <div className="sc-table__scroll">
        <table>
          <caption>People without power by campus size (estimate), synthetic grid model</caption>
          <thead>
            <tr>
              <th scope="col">Size (MW)</th>
              <th scope="col">Lines over</th>
              <th scope="col">Flexible</th>
              <th scope="col">Firm</th>
            </tr>
          </thead>
          <tbody>
            {data.sizes.map((r) => (
              <tr key={r.mw}>
                <th scope="row">{fmt(r.mw)}</th>
                <td>{fmt(r.over)}</td>
                <td>
                  {fmt(r.people_flexible)}
                  {r.cut_off ? ' (cut off)' : ''}
                </td>
                <td>
                  {fmt(r.people_firm)}
                  {r.people_firm > 0 && r.firm_held === false ? ' (cut off)' : ''}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </details>
  )
}

const plural = (n, word) => `${fmt(n)} ${n === 1 ? word : `${word}s`}`

function describe(r) {
  const flex = `${fmt(r.people_flexible)} people flexible${r.cut_off ? ', campus cut off' : ''}`
  const firm = `${fmt(r.people_firm)} firm`
  return `${fmt(r.mw)} MW: ${flex}; ${firm}; ${r.over ? `${plural(r.over, 'line')} over limit` : 'no line over limit'}.`
}

// 0 .. a round maximum: the smallest 1 / 2 / 2.5 / 5 x 10^k step that covers `max` in at most 5 steps
function niceTicks(max) {
  const p = 10 ** Math.floor(Math.log10(max / 5))
  const step = [1, 2, 2.5, 5, 10, 20].map((m) => m * p).find((s) => Math.ceil(max / s) <= 5)
  const top = Math.ceil(max / step) * step
  const ticks = []
  for (let i = 0; i * step <= top + step / 2; i++) ticks.push(Math.round(i * step))
  return { max: top, ticks }
}
