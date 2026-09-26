import { useCallback, useEffect, useId, useMemo, useState } from 'react'
import { api, assetUrl } from '../../api'
import { ErrorBanner, Field, Loading } from '../../ui'
import './pipelineProof.css'

// "We tried to break it": the fault test of the Build plans pipeline (backend/demo/gridlock/faults.py, served at
// /api/gridlock/fault-test) and the downloads in Sperry's own table format (/api/gridlock/export.*).
// Self-contained: it fetches its own data. `params` (optional) are the page's overlap settings, so the downloads
// match what's on screen: either the Build plans provider's own params ({max_km, window_months, method, utilities}:
// DESC against every Georgia utility switched on, exactly as the provider asks the engine) or explicit {a, b}. Left
// out, the engine's defaults apply. With DESC or every Georgia utility switched off there is nothing to compare, so the
// downloads say so instead of sending settings the screen isn't showing.
// The headline counts bad data only (caught = set aside + flagged); the two-digit year is a real format of the filings,
// a format test shown on its own line, never counted as a catch.

// the bad-data outcomes (the format test's "read correctly" is not a catch, so it isn't on this meter)
const OUTCOMES = [
  { id: 'set_aside', label: 'Set aside', hint: 'a blocking check failed: quarantined with the reason' },
  { id: 'flagged', label: 'Flagged', hint: 'kept, with a warning that names the problem' },
  { id: 'missed', label: 'Slipped through', hint: 'no check noticed' },
]
const GEORGIA = ['GPC', 'GTC', 'MEAG', 'DU'] // the provider's list (GridlockProvider.jsx)
const EXPORT_KEYS = ['max_km', 'window_months', 'method', 'a', 'b']
const WORDS = ['no', 'one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight', 'nine', 'ten']
const word = (k) => WORDS[k] ?? String(k)
const CSV_TABLES = [
  { id: 'overlaps', label: 'Overlaps' },
  { id: 'projects', label: 'Projects' },
  { id: 'set_aside', label: 'Set aside (with reasons)' },
  { id: 'calendar', label: 'Calendar (build and shared windows)' },
]
const n = (v) => (v == null ? '–' : Number(v).toLocaleString('en-US'))

// The faulty value with what the fault changed marked ('23O' vs '230' is invisible otherwise): the part between the
// longest common prefix and suffix of before/after. A pure deletion ('$500,00') has nothing to mark.
function Changed({ before, after }) {
  const a = String(after ?? '')
  const b = String(before ?? '')
  let p = 0
  while (p < a.length && p < b.length && a[p] === b[p]) p++
  let s = 0
  while (s < a.length - p && s < b.length - p && a[a.length - 1 - s] === b[b.length - 1 - s]) s++
  const mid = a.slice(p, a.length - s)
  if (!mid || mid.length > a.length * 0.8) return a
  return (
    <>
      {a.slice(0, p)}
      <mark className="pp-mark">{mid}</mark>
      {a.slice(a.length - s)}
    </>
  )
}

export default function PipelineProof({ params }) {
  const [state, setState] = useState({ status: 'loading', data: null, error: null })
  const [attempt, setAttempt] = useState(0)
  useEffect(() => {
    let live = true
    api('/api/gridlock/fault-test')
      .then((data) => live && setState({ status: 'ready', data, error: null }))
      .catch((error) => live && setState({ status: 'error', data: null, error }))
    return () => {
      live = false
    }
  }, [attempt])
  const retry = useCallback(() => {
    setState({ status: 'loading', data: null, error: null })
    setAttempt((a) => a + 1)
  }, [])
  const headingId = useId()
  const ready = state.status === 'ready'

  return (
    // the heading exists only once the report has loaded: until then the section names itself
    <section className="pp" aria-labelledby={ready ? headingId : undefined} aria-label={ready ? undefined : 'Fault test and downloads'}>
      {state.status === 'loading' && <Loading label="Loading the fault test…" />}
      {state.status === 'error' && <ErrorBanner error={new Error(`Fault test: ${state.error.message}`)} onRetry={retry} />}
      {ready && <FaultTest report={state.data} headingId={headingId} />}
      <Downloads params={params} />
    </section>
  )
}

function FaultTest({ report, headingId }) {
  const s = report.summary || {}
  const control = report.control || {}
  const all = report.kinds || []
  const kinds = all.filter((k) => k.origin !== 'format') // bad data: what the headline counts
  const formats = all.filter((k) => k.origin === 'format')
  const nChecks = (report.checks || []).length
  const met = kinds.filter((k) => k.origin === 'filings').length
  const common = kinds.filter((k) => k.origin === 'common').length
  return (
    <div className="pp-test">
      <p className="pp-kicker">Fault test</p>
      <h3 id={headingId} className="pp-title">
        We tried to break it: <span className="pp-hero">{n(s.caught)}</span> of {n(s.injected)} bad records caught
      </h3>
      <p className="pp-lede">
        We took the pipeline&apos;s validated records and put in {word(kinds.length)} kinds of bad data: {word(met)} it has met in
        the two filings and Sperry&apos;s sheet, {word(common)} common data-entry errors. Every copy ran back through the same
        parser and the same {nChecks || 16} checks. Nothing was re-implemented for the test.
      </p>
      <Meter summary={s} />
      <p className="pp-control">
        <strong>Control:</strong> the same {n(control.records)} records with no fault: {n(control.kept)} kept
        {control.differs_from_published?.length === 0 ? ', with the same check results the pipeline published' : ''}. So every
        catch below is the fault&apos;s doing, not a false alarm.
      </p>
      <KindTable kinds={kinds} />
      <FormatTest test={report.format_test} kinds={formats} />
      <Slipped kinds={all} />
      <NotRecoverable kinds={kinds} />
      <p className="pp-fine">
        {report.mode ? `${report.mode[0].toUpperCase()}${report.mode.slice(1)}.` : ''} Report generated{' '}
        {report.generated_at ? new Date(report.generated_at).toLocaleString('en-US', { dateStyle: 'medium', timeStyle: 'short' }) : ''};
        rerun it with <code>{report.command}</code>.
      </p>
    </div>
  )
}

function Meter({ summary }) {
  const total = summary.injected || 0
  if (!total) return null
  return (
    <figure className="pp-meter">
      <div className="pp-meter__bar" role="img" aria-label={OUTCOMES.map((o) => `${o.label} ${n(summary[o.id])}`).join(', ')}>
        {OUTCOMES.map((o) =>
          summary[o.id] ? (
            <span
              key={o.id}
              className={`pp-meter__seg pp-seg--${o.id}`}
              style={{ flexGrow: summary[o.id] }}
              title={`${o.label}: ${n(summary[o.id])} of ${n(total)} (${o.hint})`}
            />
          ) : null,
        )}
      </div>
      <figcaption className="pp-legend">
        {OUTCOMES.map((o) => (
          <span key={o.id} className="pp-legend__item" title={o.hint}>
            <span className={`pp-swatch pp-seg--${o.id}`} aria-hidden="true" />
            {o.label} <strong className={o.id === 'missed' && summary.missed ? 'pp-lost' : undefined}>{n(summary[o.id])}</strong>
          </span>
        ))}
      </figcaption>
    </figure>
  )
}

function outcomeWords(k) {
  const o = k.outcomes || {}
  const parts = []
  if (o.set_aside) parts.push(`${n(o.set_aside)} set aside`)
  if (o.flagged) parts.push(`${n(o.flagged)} flagged`)
  if (parts.length === 1) return parts[0].replace(/^[\d,]+ /, '')
  return parts.join(', ')
}

function KindTable({ kinds }) {
  return (
    <div className="pp-scroll" role="region" aria-label="Fault test by kind of bad data" tabIndex={0}>
      <table className="pp-table">
        <thead>
          <tr>
            <th scope="col">Bad data put in</th>
            <th scope="col">Example</th>
            <th scope="col" className="pp-num">
              Caught
            </th>
            <th scope="col">First caught by</th>
            <th scope="col" className="pp-num">
              Slipped through
            </th>
          </tr>
        </thead>
        <tbody>
          {kinds.map((k) => {
            const ex = (k.examples || [])[0]
            const firsts = (k.caught_by || []).filter((c) => c.first > 0)
            return (
              <tr key={k.id}>
                <th scope="row">
                  <span className="pp-kind">{k.label}</span>
                  <span className="pp-met">
                    {k.origin === 'filings' ? `Met in: ${k.met_in}` : `Common entry error: ${k.about}`}
                  </span>
                </th>
                <td className="pp-ex" data-label="Example">
                  {ex ? (
                    <>
                      <span className="pp-after" title={ex.after}>
                        <Changed before={ex.before} after={ex.after} />
                      </span>
                      <span className="pp-before" title={ex.before}>
                        was {ex.before}
                      </span>
                    </>
                  ) : (
                    '–'
                  )}
                </td>
                <td className="pp-num" data-label="Caught">
                  <span className="pp-count">
                    {n(k.caught)} of {n(k.injected)}
                  </span>
                  <span className="pp-met">{outcomeWords(k)}</span>
                </td>
                <td className="pp-by" data-label="First caught by">
                  {firsts.map((c) => (
                    <span key={c.check} className="pp-check">
                      {c.label}
                      <code>{c.check}</code>
                    </span>
                  ))}
                </td>
                <td className={`pp-num${k.missed ? ' pp-lost' : ''}`} data-label="Slipped through">{n(k.missed)}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

// the two-digit year: a real format of the filings, so nothing should "catch" it; it must be read as the right date
function FormatTest({ test, kinds }) {
  if (!test || !test.injected) return null
  const ex = (kinds[0]?.examples || [])[0]
  const ok = test.read_correctly === test.injected
  const by = ex?.noted_by?.[0]
  return (
    <div className="pp-format">
      <h4>Format test, not counted above</h4>
      <p>
        <strong className={ok ? undefined : 'pp-lost'}>
          {n(test.read_correctly)} of {n(test.injected)}
        </strong>{' '}
        dates rewritten with a two-digit year, the way DESC files them, were read as the right date. It is a real format of
        the filings, not bad data, so no check should catch it.
        {ex ? (
          <>
            {' '}
            Example: <code>{ex.after}</code> for {ex.before}
            {ex.detail ? <>: &ldquo;{ex.detail}&rdquo;</> : null}
            {by ? (
              <>
                {' '}
                (<code>{by}</code>)
              </>
            ) : null}
            .
          </>
        ) : null}
        {ok ? '' : ` ${n(test.not_read_correctly)} were not read correctly: listed below.`}
      </p>
    </div>
  )
}

// the misses, grouped by their reason (the report lists every one)
function Slipped({ kinds }) {
  const groups = useMemo(() => {
    const out = []
    for (const k of kinds) {
      const byWhy = new Map()
      for (const m of k.misses || []) {
        const why = m.why_missed || 'no check noticed'
        if (!byWhy.has(why)) byWhy.set(why, [])
        byWhy.get(why).push(m)
      }
      for (const [why, list] of byWhy) out.push({ kind: k.label, why, list })
    }
    return out.sort((a, b) => b.list.length - a.list.length)
  }, [kinds])
  const total = groups.reduce((t, g) => t + g.list.length, 0)
  if (!total) return <p className="pp-note">Nothing slipped through.</p>
  return (
    <div className="pp-slipped">
      <h4>
        The {n(total)} that slipped through, and why
      </h4>
      <ul>
        {groups.map((g) => (
          <li key={`${g.kind}-${g.why}`}>
            <span className="pp-slipped__n pp-lost">{n(g.list.length)}</span>
            <div>
              <strong>{g.kind}:</strong> {g.why}.
              <details>
                <summary>Which records</summary>
                <ul className="pp-records">
                  {g.list.map((m, i) => (
                    <li key={`${m.record}-${i}`}>
                      <code>{m.record}</code> {m.before} → {m.after}
                    </li>
                  ))}
                </ul>
              </details>
            </div>
          </li>
        ))}
      </ul>
    </div>
  )
}

// caught (flagged for a person), but the value itself can't be put right automatically
function NotRecoverable({ kinds }) {
  const rows = kinds.filter((k) => (k.value_wrong || []).length)
  if (!rows.length) return null
  return (
    <div className="pp-note">
      <h4>Caught, but not repaired</h4>
      <ul>
        {rows.map((k) => (
          <li key={k.id}>
            <strong>
              {k.label}, {n(k.value_wrong.length)}:
            </strong>{' '}
            {k.value_wrong[0].note}
            {k.value_wrong.length > 1 ? ` (${k.value_wrong.map((w) => w.record).join(', ')})` : ` (${k.value_wrong[0].record})`}.
          </li>
        ))}
      </ul>
    </div>
  )
}

// The query the downloads send: the screen's settings. From the provider's params, DESC x every Georgia utility switched
// on (as GridlockProvider asks the engine); null when that leaves nothing to compare.
function exportQuery(params) {
  const src = { ...(params || {}) }
  if (src.utilities) {
    const b = GEORGIA.filter((u) => src.utilities[u])
    if (!src.utilities.DESC || !b.length) return null
    src.a = 'DESC'
    src.b = b.join(',')
  }
  const p = new URLSearchParams()
  for (const k of EXPORT_KEYS) if (src[k] !== undefined && src[k] !== null && src[k] !== '') p.set(k, src[k])
  return p.toString()
}

function Downloads({ params }) {
  const [table, setTable] = useState('overlaps')
  const q = useMemo(() => exportQuery(params), [params])
  const href = (path, extra) => {
    const p = new URLSearchParams(q)
    for (const [k, v] of Object.entries(extra || {})) p.set(k, v)
    const s = p.toString()
    return assetUrl(`/api/gridlock/${path}${s ? `?${s}` : ''}`)
  }
  return (
    <div className="pp-dl">
      <h4>Download the tables</h4>
      <p className="pp-lede">
        In Sperry&apos;s own format: their columns, in their order, then ours to the right (closest-point distance, tier, score,
        location confidence, the PDF page, the OpenStreetMap feature, the checks each record passed). Every validated project and
        every flagged overlap at these settings.
      </p>
      {q === null ? (
        <p className="pp-note" role="status">
          Switch on DESC and at least one Georgia utility to download the overlaps at the settings on screen.
        </p>
      ) : (
      <div className="pp-dl__row">
        <a className="btn" href={href('export.xlsx')} download>
          Excel workbook (Sperry&apos;s format)
        </a>
        <div className="pp-dl__csv">
          <Field label="CSV table" as="select" value={table} onChange={(e) => setTable(e.target.value)}>
            {CSV_TABLES.map((t) => (
              <option key={t.id} value={t.id}>
                {t.label}
              </option>
            ))}
          </Field>
          <a className="btn btn--secondary" href={href('export.csv', { table })} download>
            CSV
          </a>
        </div>
        <a className="btn btn--secondary" href={href('export.geojson')} download>
          GeoJSON (map layers)
        </a>
      </div>
      )}
      <p className="pp-fine">
        Generated from public filings (DESC 2024-2028 list; Georgia 2025 IRP Vol. 3 public disclosure); locations approximate where
        marked; not an official utility record. Locations © OpenStreetMap contributors (ODbL).
      </p>
    </div>
  )
}
