// Two small charts that build year by year (one measure each, one axis each): the busiest line's loading (% of its
// rating, with the 100 % rating marked) and how many lines and transformers run over their rating. Years not reached
// yet stay as faint slots, so the reader sees the whole span from the start. Each mark has a native tooltip.
const W = 320
const H = 96
const PAD = { l: 34, r: 8, t: 10, b: 18 }
const IW = W - PAD.l - PAD.r
const IH = H - PAD.t - PAD.b

const xOf = (k, n) => PAD.l + (n <= 1 ? IW / 2 : (k * IW) / (n - 1))
const shortYear = (f) => (f.year == null ? 'now' : `’${String(f.year).slice(2)}`)

function niceMax(v, floor) {
  const m = Math.max(v, floor)
  const step = m > 200 ? 100 : m > 50 ? 25 : m > 10 ? 5 : 2
  return Math.ceil(m / step) * step
}

export function PeakChart({ frames, idx }) {
  const n = frames.length
  const max = niceMax(Math.max(...frames.map((f) => f.strain.peak_pct)), 120)
  const y = (v) => PAD.t + IH - (Math.min(v, max) / max) * IH
  const pts = frames.slice(0, idx + 1).map((f, k) => [xOf(k, n), y(f.strain.peak_pct)])
  const last = frames[idx]
  return (
    <figure className="tl-chart">
      <figcaption className="tl-chart__title">
        Busiest line or transformer <span className="tl-chart__unit">% of its rating</span>
      </figcaption>
      <svg viewBox={`0 0 ${W} ${H}`} className="tl-chart__svg" aria-hidden="true">
        {[0, max].map((v) => (
          <text key={v} className="tl-axis" x={PAD.l - 6} y={y(v) + 3} textAnchor="end">
            {v}
          </text>
        ))}
        <line className="tl-rule" x1={PAD.l} x2={W - PAD.r} y1={y(100)} y2={y(100)} />
        <text className="tl-rule__label" x={W - PAD.r} y={y(100) - 3} textAnchor="end">
          rating
        </text>
        {frames.map((f, k) => (
          <text key={k} className={`tl-axis${k === idx ? ' tl-axis--on' : ''}`} x={xOf(k, n)} y={H - 4} textAnchor="middle">
            {shortYear(f)}
          </text>
        ))}
        {frames.map((f, k) => k > idx && <circle key={`s${k}`} className="tl-slot" cx={xOf(k, n)} cy={y(0)} r={1.5} />)}
        {pts.length > 1 && <polyline className="tl-peak" points={pts.map((p) => p.join(',')).join(' ')} />}
        {pts.map((p, k) => (
          <circle key={k} className={`tl-peak__dot${frames[k].strain.peak_pct >= 100 ? ' tl-peak__dot--over' : ''}`} cx={p[0]} cy={p[1]} r={k === idx ? 4 : 2.5}>
            <title>{`${frames[k].year ?? 'The grid alone'}: ${frames[k].strain.peak_pct} %`}</title>
          </circle>
        ))}
      </svg>
      <p className="tl-chart__now">
        {last.year ?? 'Grid alone'}: <strong>{Math.round(last.strain.peak_pct)} %</strong>
      </p>
    </figure>
  )
}

export function OverChart({ frames, idx, growth }) {
  const n = frames.length
  const max = niceMax(Math.max(...frames.map((f) => f.strain.over)), 4)
  const y = (v) => PAD.t + IH - (v / max) * IH
  const bw = Math.max(6, Math.min(16, IW / n - 6))
  const last = frames[idx]
  return (
    <figure className="tl-chart">
      <figcaption className="tl-chart__title">
        Lines and transformers <span className="tl-chart__unit">over their rating</span>
      </figcaption>
      <svg viewBox={`0 0 ${W} ${H}`} className="tl-chart__svg" aria-hidden="true">
        {[0, max].map((v) => (
          <text key={v} className="tl-axis" x={PAD.l - 6} y={y(v) + 3} textAnchor="end">
            {v}
          </text>
        ))}
        <line className="tl-base" x1={PAD.l} x2={W - PAD.r} y1={y(0)} y2={y(0)} />
        {frames.map((f, k) => (
          <text key={k} className={`tl-axis${k === idx ? ' tl-axis--on' : ''}`} x={xOf(k, n)} y={H - 4} textAnchor="middle">
            {shortYear(f)}
          </text>
        ))}
        {frames.map((f, k) => {
          const x = xOf(k, n) - bw / 2
          if (k > idx) return <rect key={k} className="tl-bar tl-bar--slot" x={x} y={y(0) - 1} width={bw} height={1} />
          const v = f.strain.over
          const h = Math.max(v > 0 ? 2 : 0, y(0) - y(v))
          return (
            <g key={k}>
              <rect className={`tl-bar${k === idx ? ' tl-bar--on' : ''}`} x={x} y={y(0) - h} width={bw} height={h} rx={Math.min(3, bw / 3)}>
                <title>{`${f.year ?? 'The grid alone'}: ${v} over (${f.strain.over_lines} lines, ${f.strain.over_transformers} transformers)`}</title>
              </rect>
              {growth && f.alone && f.alone.over > 0 && <line className="tl-alone" x1={x - 2} x2={x + bw + 2} y1={y(f.alone.over)} y2={y(f.alone.over)} />}
            </g>
          )
        })}
      </svg>
      <p className="tl-chart__now">
        {last.year ?? 'Grid alone'}: <strong>{last.strain.over}</strong> over{' '}
        <span className="muted">
          ({last.strain.over_lines} {last.strain.over_lines === 1 ? 'line' : 'lines'}, {last.strain.over_transformers}{' '}
          {last.strain.over_transformers === 1 ? 'transformer' : 'transformers'})
        </span>
        {growth && last.alone && last.alone.over > 0 && <span className="tl-chart__alone"> · tick: {last.alone.over} from the load growth alone</span>}
      </p>
    </figure>
  )
}
