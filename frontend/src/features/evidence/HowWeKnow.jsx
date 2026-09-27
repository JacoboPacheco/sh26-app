// FEATURE: "How we know" (CLAUDE.md -> Decisions -> DEPTH: "show the engineering"). A small fold, closed by default, that
// answers "how do you know?" for the case on screen with three engine checks (backend/evidence.py), asked for only once
// it is opened:
//   1. the engine against the dataset's own solved flows (validation.json + the gap as a share of each line's rating)
//   2. an N-1 screen of the fix: the most loaded lines near the site taken out one at a time, with the fix, without it,
//      and on today's grid with nothing added
//   3. how often it would overload: the lowest load level that puts a line past its rating, and hours a year on a
//      coarse step curve (labeled so)
// plus, optionally, the formulas of the headline numbers beside it (Formula.jsx: 'people_hit' | 'cost' |
// 'outage_hours' | 'capacity'), with the case's own inputs from fields the API already returns.
// Props: body (a case: the store's caseBody, or the incident stage's body with a catastrophe `preset`; without one,
// only the validation and the formulas show), applied (the case on screen already has its fix in it: the results
// panel's flip; then the checks judge the case as it is, never another fix), region, figures (kinds of Formula to show
// first), cascade / cost / capacity / target / flex / meta (Formula's inputs), label (the fold's summary), defaultOpen.
// The case is asked about only once it has settled for SETTLE_MS (a size slider moving with the fold open would
// otherwise fire two POSTs per settle, and the whole venue shares one IP's rate limit).
// Sober (LOOK): plain tabular figures, red only for a line past its rating, green only for a check that holds.
// Estimates on a synthetic grid model (Breakthrough Energy / Texas A&M), never a real utility's network.
import { useCallback, useEffect, useMemo, useState } from 'react'
import { fmt } from '../../geo'
import { Loading } from '../../ui'
import { caseOf, checkable, getHours, getN1, getValidation } from './evidenceApi'
import Formula from './Formula'
import './evidence.css'

export { default as Formula } from './Formula'

const SETTLE_MS = 400

// `value` once it has stopped changing for `ms` (the first value at once)
function useSettled(value, ms) {
  const [settled, setSettled] = useState(value)
  useEffect(() => {
    if (value === settled) return undefined
    const t = setTimeout(() => setSettled(value), ms)
    return () => clearTimeout(t)
  }, [value, settled, ms])
  return settled
}

// a request's life: {status: loading | done | error, data, error, retry}
function useCheck(key, load) {
  const [s, setS] = useState({ key: null, status: 'idle', data: null, error: null })
  const [n, setN] = useState(0)
  useEffect(() => {
    if (!key) return undefined
    let live = true
    setS({ key, status: 'loading', data: null, error: null })
    load().then(
      (data) => live && setS({ key, status: 'done', data, error: null }),
      (error) => live && setS({ key, status: 'error', data: null, error }),
    )
    return () => {
      live = false
    }
    // `load` is rebuilt each render with the same key; the key says when to ask again
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, n])
  const retry = useCallback(() => setN((x) => x + 1), [])
  return s.key === key ? { ...s, retry } : { status: key ? 'loading' : 'idle', data: null, error: null, retry }
}

// never "Failed to fetch": a network failure reads as a plain sentence; a server's own sentence is kept
function Failed({ error, onRetry, what }) {
  const msg = error instanceof TypeError || !error?.message || /fetch/i.test(error.message) ? `The ${what} didn’t load: the server didn’t answer.` : `The ${what} didn’t load: ${error.message.replace(/\.?$/, '.')}`
  return (
    <p className="hwk__fail" role="alert">
      {msg}{' '}
      <button type="button" className="hwk__retry" onClick={onRetry}>
        Retry
      </button>
    </p>
  )
}

const pctText = (p) => (p == null ? '–' : p > 300 ? 'over 300 %' : `${Math.round(p)} %`)
const share = (x) => (x == null ? '–' : x >= 0.995 ? `${(x * 100).toFixed(2)} %` : `${(x * 100).toFixed(1)} %`)
const mw = (v) => (v == null ? '–' : `${Number(v).toLocaleString('en-US', { minimumFractionDigits: v < 100 ? 1 : 0, maximumFractionDigits: 1 })} MW`)

function Method({ label, text, limits, source }) {
  return (
    <details className="hwk__method">
      <summary>Method, limits and source</summary>
      {label && <p className="hwk__label">{label}</p>}
      {text && <p>{text}</p>}
      {limits && <p>{limits}</p>}
      {source?.url && (
        <p>
          <a href={source.url} target="_blank" rel="noreferrer">
            {source.name}
          </a>
        </p>
      )}
    </details>
  )
}

// ------------------------------------------------------------------ 1. validation
function Validation({ region }) {
  const q = useCheck(region ? `v|${region}` : null, () => getValidation(region))
  return (
    <section className="hwk__check" aria-label="Validation">
      <h3 className="hwk__h">The engine matches the dataset&apos;s own solved flows</h3>
      {q.status === 'loading' && <Loading label="Loading the validation…" />}
      {q.status === 'error' && <Failed error={q.error} onRetry={q.retry} what="validation" />}
      {q.data && <ValidationBody v={q.data} />}
    </section>
  )
}

function ValidationBody({ v }) {
  const st = v.state || {}
  const gap = v.rating_gap || {}
  const near = gap.near_limit || {}
  const sum = v.summary || {}
  return (
    <>
      <p className="hwk__lead">
        {v.region_name}: our DC power flow and the dataset&apos;s published solution agree line by line, before anything is added.
      </p>
      <dl className="hwk__stats">
        <div>
          <dt>Correlation</dt>
          <dd>{st.corr != null ? st.corr.toFixed(4) : '–'}</dd>
        </div>
        <div>
          <dt>Lines compared</dt>
          <dd>{st.compared != null ? fmt(st.compared) : '–'}</dd>
        </div>
        <div>
          <dt>Median gap</dt>
          <dd>{mw(st.median_abs_err_mw)}</dd>
        </div>
        <div>
          <dt>95 % of lines within</dt>
          <dd>{mw(st.p95_abs_err_mw)}</dd>
        </div>
        <div>
          <dt>Within 5 % of the line&apos;s rating</dt>
          <dd>{share(gap.within_5pct_of_rating_share)}</dd>
        </div>
        {near.lines > 0 && (
          <div>
            <dt>Near their limit ({fmt(near.lines)} lines)</dt>
            <dd>{near.median_pp != null ? `${near.median_pp.toFixed(1)} pts` : '–'}</dd>
          </div>
        )}
      </dl>
      <p className="hwk__note">
        {share(st.within_2mw_or_5pct_share)} of lines are within 2 MW or 5 % of the dataset&apos;s own flow.
        {near.lines > 0 && ` On the ${fmt(near.lines)} lines the dataset loads at ${near.threshold_pct ?? 80} % or more, ours is ${near.median_pp?.toFixed(1)} points of loading away at the median, ${near.max_pp?.toFixed(1)} at most.`}
        {sum.states ? ` All ${fmt(sum.passed)} of ${fmt(sum.states)} state models pass (correlation at least ${v.threshold?.corr_min ?? 0.9}).` : ''}
      </p>
      <Method label={v.label} text={`${v.method || ''} ${v.rating_gap_method || ''}`.trim()} limits={[v.reference, v.limits].filter(Boolean).join(' ')} source={v.source} />
    </>
  )
}

// ------------------------------------------------------------------ 2. the N-1 screen
function N1({ c, applied }) {
  const key = c ? JSON.stringify(c) : null
  const q = useCheck(key ? `n1|${!!applied}|${key}` : null, () => getN1(c, applied))
  return (
    <section className="hwk__check" aria-label="N-1 screen">
      <h3 className="hwk__h">Does it hold if one more line fails?</h3>
      {q.status === 'loading' && <Loading label="Taking lines out one at a time…" />}
      {q.status === 'error' && <Failed error={q.error} onRetry={q.retry} what="N-1 screen" />}
      {q.data && <N1Body r={q.data} />}
    </section>
  )
}

function Worst({ w }) {
  if (!w) return null
  const over = w.pct > 100
  return (
    <span className="hwk__worst">
      Worst: {w.out.name} out, {w.over.name} at <b className={over ? 'hwk__bad' : undefined}>{pctText(w.pct)}</b> of its rating
      {w.over.rating_estimated ? ' (an estimated rating)' : ''}
    </span>
  )
}

function N1Body({ r }) {
  const n = r.screened
  return (
    <>
      <p className="hwk__lead">{r.sentence}</p>
      <ul className="hwk__vars">
        {r.variants.map((v) => {
          const bad = v.fails_any > 0
          return (
            <li key={v.key}>
              <span className="hwk__var">
                <span className="hwk__var-k">{v.label}</span>
                <span className={`hwk__var-n ${bad ? 'hwk__bad' : 'hwk__ok'}`}>
                  {bad ? `${fmt(v.fails_any)} of ${fmt(n)} ${v.fails_any === 1 ? 'fails' : 'fail'}` : `holds all ${fmt(n)}`}
                </span>
              </span>
              {v.over_before > 0 && (
                <span className="hwk__worst">
                  {fmt(v.over_before)} {v.over_before === 1 ? 'line is' : 'lines are'} over before anything fails.
                </span>
              )}
              {bad && <Worst w={v.worst} />}
            </li>
          )
        })}
      </ul>
      {r.fix?.source === 'applied' && !r.fix?.count && <p className="hwk__note">What was checked: {r.fix.words}.</p>}
      {r.fix?.source !== 'none' && r.fix?.count > 0 && (
        <p className="hwk__note">
          What was checked: {r.fix.words}.{' '}
          {r.fix.elements
            .slice(0, 3)
            .map((e) => `${e.name}, ${fmt(e.from_mva)} to ${fmt(e.to_mva)} MVA`)
            .join('; ')}
          {r.fix.count > 3 ? `; and ${fmt(r.fix.count - 3)} more.` : '.'}
        </p>
      )}
      {r.strands?.count > 0 && (
        <p className="hwk__note">
          {fmt(r.strands.count)} {r.strands.count === 1 ? 'outage cuts' : 'outages cut'} off load on {r.strands.count === 1 ? 'its' : 'their'} own, the only path to it
          {r.strands.examples[0] ? ` (${r.strands.examples[0].out.name}: ${mw(r.strands.examples[0].lost_mw)}${r.strands.examples[0].campus ? ', the campus too' : ''})` : ''}.
        </p>
      )}
      <Method label={r.label} text={r.method} limits={r.limits} source={r.source} />
    </>
  )
}

// ------------------------------------------------------------------ 3. hours a year
function Hours({ c, applied }) {
  const key = c ? JSON.stringify(c) : null
  const q = useCheck(key ? `h|${!!applied}|${key}` : null, () => getHours(c, applied))
  return (
    <section className="hwk__check" aria-label="How often">
      <h3 className="hwk__h">How often would it overload?</h3>
      {q.status === 'loading' && <Loading label="Raising the load step by step…" />}
      {q.status === 'error' && <Failed error={q.error} onRetry={q.retry} what="how-often check" />}
      {q.data && <HoursBody r={q.data} />}
    </section>
  )
}

function hoursText(v) {
  const h = v.hours
  if (!h) return '–'
  if (!v.counted?.length) return v.bands?.length ? 'none counted' : 'never'
  if (h.about == null) return h.high ? `under ${fmt(h.high)} h a year` : 'never'
  if (h.about >= 8760) return 'every hour'
  if (h.about === 0) return 'never'
  return `~${fmt(h.about)} h a year`
}

// "from 46.7 %" or "68.4–77.3 % and from 100.3 %" (shares of the summer peak)
const ranges = (counted) => counted.map(([a, b]) => (b >= 140 ? `from ${a} %` : `${a}–${b} %`)).join(' and ')

function bandWords(v) {
  const c = v.counted || []
  let s
  if (!c.length) s = v.calm_from_pct != null ? `Within its ratings from ${v.calm_from_pct} % of the summer peak up to 140 %.` : 'Within its ratings up to 140 % of the summer peak.'
  else if (c.length === 1 && c[0][1] >= 140) s = c[0][0] <= 40 ? 'Past a rating at every level from 40 % of the summer peak up.' : `First line past its rating at ${c[0][0]} % of the summer peak.`
  else s = `Past a rating ${ranges(c)} of the summer peak.`
  if (v.low_to_pct != null) s += ` Also over below ${v.low_to_pct} %, not counted: the model holds its flows to neighbouring states fixed as its own load falls.`
  const h = v.hours
  if (c.length && h?.about != null && h.low !== h.high && h.about > 0 && h.about < 8760) s += ` Between ${fmt(h.low)} and ${fmt(h.high)} h on the curve.`
  return s
}

function HoursBody({ r }) {
  return (
    <>
      <p className="hwk__lead">{r.sentence}</p>
      <ul className="hwk__vars">
        {r.variants.map((v) => (
          <li key={v.key}>
            <span className="hwk__var">
              <span className="hwk__var-k">{v.label}</span>
              {/* red only when it is over at the case's own hour; never green: an hour a year past a rating is not a pass */}
              <span className={`hwk__var-n${v.key !== 'grid_alone' && v.at_case ? ' hwk__bad' : ''}`}>{hoursText(v)}</span>
            </span>
            <span className="hwk__worst">{bandWords(v)}</span>
          </li>
        ))}
      </ul>
      <p className="hwk__note">This case runs at {r.case_pct} % of the summer peak. Estimates.</p>
      <details className="hwk__method">
        <summary>The coarse curve</summary>
        <p>{r.curve_label}</p>
        <ul className="hwk__curve">
          {r.curve.map((s) => (
            <li key={s.level}>
              <span className="hwk__curve-k">{s.pct} % or more</span>
              <span className="hwk__curve-n">{fmt(s.hours)} h</span>
              <span className="hwk__curve-w">{s.what}</span>
            </li>
          ))}
        </ul>
      </details>
      <Method label={r.label} text={r.method} limits={r.limits} source={r.source} />
    </>
  )
}

// ------------------------------------------------------------------ the fold
export default function HowWeKnow({ body, applied = false, region, figures = [], cascade, cost, capacity, target, flex, meta, label = 'How we know', defaultOpen = false }) {
  const [open, setOpen] = useState(defaultOpen)
  const key = useSettled(body ? JSON.stringify(caseOf(body)) : '', SETTLE_MS)
  const c = useMemo(() => (key ? JSON.parse(key) : null), [key])
  const reg = region || c?.region || 'FL'
  const withCase = checkable(c)
  return (
    <details className="hwk" open={defaultOpen || undefined} onToggle={(e) => setOpen(e.currentTarget.open)}>
      <summary>{label}</summary>
      {open && (
        <div className="hwk__body">
          {figures.length > 0 && (
            <div className="hwk__figs">
              <h3 className="hwk__group">How the numbers are made</h3>
              {figures.map((k) => (
                <Formula key={k} kind={k} cascade={cascade} cost={cost} capacity={capacity} target={target} flex={flex} meta={meta} />
              ))}
            </div>
          )}
          <h3 className="hwk__group">The engine&apos;s checks</h3>
          <Validation region={reg} />
          {withCase && <N1 c={c} applied={applied} />}
          {withCase && <Hours c={c} applied={applied} />}
          <p className="hwk__foot">Checks on a synthetic grid model (Breakthrough Energy / Texas A&amp;M), not any utility&apos;s network. DC power flow, steady state.</p>
        </div>
      )}
    </details>
  )
}
