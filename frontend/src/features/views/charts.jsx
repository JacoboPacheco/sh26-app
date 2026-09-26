import { FUELS } from './fuels'
import { niceTicks, useTip, useWidth } from './viewsKit'

// Hand-made SVG charts for the Views page (no chart library). One axis, thin marks with a rounded data end,
// values written on the marks, a recessive grid, a tooltip on every row, a legend for every stack and a table
// view for every chart. Colors are the tokens in views.css (the dark steps of the validated categorical palette).

// a bar with a square base and a rounded data end (right side), 4px radius per the mark spec
function barPath(x, y, w, h, r = 4) {
  if (w <= 0) return ''
  const rr = Math.min(r, w, h / 2)
  return `M${x} ${y}H${x + w - rr}A${rr} ${rr} 0 0 1 ${x + w} ${y + rr}V${y + h - rr}A${rr} ${rr} 0 0 1 ${x + w - rr} ${y + h}H${x}Z`
}

export function Tip({ tip }) {
  if (!tip) return null
  const flip = tip.x > tip.w - 230
  return (
    <div className="vw-tip" role="tooltip" style={{ left: flip ? undefined : tip.x + 14, right: flip ? tip.w - tip.x + 14 : undefined, top: Math.max(tip.y - 8, 0) }}>
      {tip.content}
    </div>
  )
}

export function Swatch({ color, ring }) {
  return <span className={ring ? 'vw-swatch vw-swatch--ring' : 'vw-swatch'} style={{ '--sw': color }} aria-hidden="true" />
}

export function Legend({ items, extra }) {
  return (
    <ul className="vw-legend" aria-label="Legend">
      {items.map((it) => (
        <li key={it.id}>
          <Swatch color={it.color} />
          {it.label}
        </li>
      ))}
      {extra}
    </ul>
  )
}

// ------------------------------------------------------------------------------------ ranked bars
// rows: [{key, label, value, tip: [[label, text], ...]}] (already sorted). One series, one color.
export function RankedBars({ rows, fmtValue, selectedKey, onSelect, ariaLabel, unit }) {
  const [boxRef, width] = useWidth()
  const { tip, show, hide } = useTip(boxRef)
  const narrow = width < 460
  const labelW = narrow ? 92 : 128
  const valueW = narrow ? 58 : 76
  const rowH = 24
  const top = 4
  const axisH = 22
  const barX = labelW
  const barW = Math.max(width - labelW - valueW, 40)
  const max = Math.max(...rows.map((r) => r.value), 1)
  const ticks = niceTicks(max, narrow ? 3 : 5)
  const axisMax = ticks[ticks.length - 1] || max
  const sx = (v) => (v / axisMax) * barW
  const H = top + rows.length * rowH + axisH
  return (
    <div className="vw-chart" ref={boxRef}>
      {width > 0 && (
        <svg width={width} height={H} role="img" aria-label={ariaLabel} onPointerLeave={hide}>
          {ticks.map((t) => (
            <g key={t}>
              <line x1={barX + sx(t)} x2={barX + sx(t)} y1={top} y2={top + rows.length * rowH} className="vw-grid" />
              <text x={barX + sx(t)} y={H - 6} textAnchor="middle" className="vw-axis">
                {fmtValue(t, true)}
              </text>
            </g>
          ))}
          {rows.map((r, i) => {
            const y = top + i * rowH
            const on = selectedKey === r.key
            const w = sx(r.value)
            return (
              <g
                key={r.key}
                className={on ? 'vw-row vw-row--on' : 'vw-row'}
                tabIndex={onSelect ? 0 : undefined}
                role={onSelect ? 'button' : undefined}
                aria-pressed={onSelect ? on : undefined}
                aria-label={`${r.label}: ${fmtValue(r.value)}${unit ? ` ${unit}` : ''}`}
                onPointerMove={(e) => show(e, <TipBody title={r.label} lines={r.tip} />)}
                onFocus={(e) => {
                  const b = e.currentTarget.getBoundingClientRect()
                  show({ clientX: b.left + Math.min(b.width, 260), clientY: b.top + rowH / 2 }, <TipBody title={r.label} lines={r.tip} />)
                }}
                onBlur={hide}
                onClick={() => onSelect?.(r.key)}
                onKeyDown={(e) => {
                  if (onSelect && (e.key === 'Enter' || e.key === ' ')) {
                    e.preventDefault()
                    onSelect(r.key)
                  }
                }}
              >
                <rect x={0} y={y} width={width} height={rowH} className="vw-row__bg" />
                <text x={labelW - 10} y={y + rowH / 2 + 4} textAnchor="end" className="vw-label">
                  {r.label}
                </text>
                <path d={barPath(barX, y + 5, Math.max(w, 1.5), rowH - 10)} className="vw-bar" />
                <text x={barX + w + 8} y={y + rowH / 2 + 4} className="vw-value">
                  {fmtValue(r.value)}
                </text>
              </g>
            )
          })}
        </svg>
      )}
      <Tip tip={tip} />
    </div>
  )
}

function TipBody({ title, lines = [] }) {
  return (
    <>
      <strong>{title}</strong>
      {lines.map(([k, v]) => (
        <span key={k} className="vw-tip__row">
          <span>{k}</span>
          <span>{v}</span>
        </span>
      ))}
    </>
  )
}

// ------------------------------------------------------------------------------------ stacked bars
// rows: [{key, label, segs: {fuelId: value}, marker, tip}]. `marker` is a value drawn as a vertical tick (the model's load).
export function StackedBars({ rows, fmtValue, selectedKey, onSelect, ariaLabel, axisMax: fixedMax, markerLabel }) {
  const [boxRef, width] = useWidth()
  const { tip, show, hide } = useTip(boxRef)
  const narrow = width < 460
  const labelW = narrow ? 92 : 128
  const rightPad = 14
  const rowH = 26
  const top = 6
  const axisH = 22
  const barX = labelW
  const barW = Math.max(width - labelW - rightPad, 40)
  const totals = rows.map((r) => Object.values(r.segs).reduce((a, b) => a + b, 0))
  const max = fixedMax || Math.max(...totals, ...rows.map((r) => r.marker || 0), 1)
  const ticks = niceTicks(max, narrow ? 3 : 5)
  const axisMax = ticks[ticks.length - 1] || max
  const sx = (v) => (v / axisMax) * barW
  const H = top + rows.length * rowH + axisH
  return (
    <div className="vw-chart" ref={boxRef}>
      {width > 0 && (
        <svg width={width} height={H} role="img" aria-label={ariaLabel} onPointerLeave={hide}>
          {ticks.map((t) => (
            <g key={t}>
              <line x1={barX + sx(t)} x2={barX + sx(t)} y1={top} y2={top + rows.length * rowH} className="vw-grid" />
              <text x={barX + sx(t)} y={H - 6} textAnchor="middle" className="vw-axis">
                {fmtValue(t, true)}
              </text>
            </g>
          ))}
          {rows.map((r, i) => {
            const y = top + i * rowH
            const on = selectedKey === r.key
            let x = barX
            return (
              <g
                key={r.key}
                className={on ? 'vw-row vw-row--on' : 'vw-row'}
                tabIndex={onSelect ? 0 : undefined}
                role={onSelect ? 'button' : undefined}
                aria-pressed={onSelect ? on : undefined}
                aria-label={`${r.label}: ${fmtValue(totals[i])} of generation capacity${r.marker ? `, model load ${fmtValue(r.marker)}` : ''}`}
                onPointerMove={(e) => show(e, <TipBody title={r.label} lines={r.tip} />)}
                onFocus={(e) => {
                  const b = e.currentTarget.getBoundingClientRect()
                  show({ clientX: b.left + Math.min(b.width, 260), clientY: b.top + rowH / 2 }, <TipBody title={r.label} lines={r.tip} />)
                }}
                onBlur={hide}
                onClick={() => onSelect?.(r.key)}
                onKeyDown={(e) => {
                  if (onSelect && (e.key === 'Enter' || e.key === ' ')) {
                    e.preventDefault()
                    onSelect(r.key)
                  }
                }}
              >
                <rect x={0} y={y} width={width} height={rowH} className="vw-row__bg" />
                <text x={labelW - 10} y={y + rowH / 2 + 4} textAnchor="end" className="vw-label">
                  {r.label}
                </text>
                {FUELS.map((f) => {
                  const v = r.segs[f.id] || 0
                  const w = sx(v)
                  const x0 = x
                  x += w
                  if (w < 0.6) return null
                  const gap = w > 3 ? 1 : 0
                  return <rect key={f.id} x={x0 + gap} y={y + 6} width={Math.max(w - 2 * gap, 0.6)} height={rowH - 12} fill={f.color} className="vw-seg" />
                })}
                {r.marker ? <rect x={barX + sx(r.marker) - 1} y={y + 2} width={2} height={rowH - 4} className="vw-marker" /> : null}
              </g>
            )
          })}
        </svg>
      )}
      <Tip tip={tip} />
      {markerLabel && (
        <p className="vw-note vw-note--legend">
          <span className="vw-marker-key" aria-hidden="true" /> {markerLabel}
        </p>
      )}
    </div>
  )
}

// ------------------------------------------------------------------------------------ one 100 % bar
export function ShareBar({ parts, ariaLabel }) {
  const total = parts.reduce((a, p) => a + p.value, 0) || 1
  return (
    <div className="vw-share" role="img" aria-label={ariaLabel}>
      {parts.map((p) => {
        const pct = (100 * p.value) / total
        return (
          <span key={p.id} className="vw-share__seg" style={{ width: `${pct}%`, background: p.color }} title={`${p.label}: ${Math.round(pct * 10) / 10}%`}>
            {pct >= 7 ? <span>{p.label}</span> : null}
          </span>
        )
      })}
    </div>
  )
}

// ------------------------------------------------------------------------------------ table view
export function ChartTable({ columns, rows, caption }) {
  return (
    <div className="vw-tablewrap">
      <table className="vw-table">
        <caption>{caption}</caption>
        <thead>
          <tr>
            {columns.map((c) => (
              <th key={c.key} scope="col" className={c.num ? 'num' : undefined}>
                {c.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.key}>
              {columns.map((c) => (
                <td key={c.key} className={c.num ? 'num' : undefined}>
                  {r[c.key]}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
