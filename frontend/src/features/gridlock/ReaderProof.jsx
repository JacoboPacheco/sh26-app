import { useCallback, useEffect, useId, useMemo, useState } from 'react'
import { api } from '../../api'
import { Badge, ErrorBanner, Loading } from '../../ui'
import './readerProof.css'

// Reader C: Gemini read the two filings' PDF pages on its own (backend/demo/gridlock/gemini_reader.py, offline) and filled the
// same fields as the two parsers. This panel shows how often the three readers agree, every disagreement with its PDF page, and
// Gemini's proposals for the records the checks set aside, with the checks' verdicts. Gemini is advisory: the published records
// come from the parsers, and a rescue counts only when the pipeline's own checks accept it.
// Self-contained: fetches GET /api/gridlock/reader (a committed file; no Gemini call at view time).

const n = (v) => (v == null ? '–' : Number(v).toLocaleString('en-US'))
const pct = (v) => (v == null ? '–' : `${Number(v).toLocaleString('en-US', { maximumFractionDigits: 1 })} %`)
const READER_ORDER = ['parser_a', 'parser_b', 'gemini']
const STATUS_WORDS = {
  gemini_differs: 'Gemini differs from both parsers',
  parser_b_differs: 'Parser B differs',
  parser_a_differs: 'Parser A differs',
  all_differ: 'All three differ',
}
const FIRST_SHOWN = 8

export default function ReaderProof() {
  const [state, setState] = useState({ status: 'loading', data: null, error: null })
  const [attempt, setAttempt] = useState(0)
  useEffect(() => {
    let live = true
    api('/api/gridlock/reader')
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
  return (
    <section className="rp" aria-labelledby={headingId}>
      {state.status === 'loading' && <Loading label="Loading the third reader…" />}
      {state.status === 'error' && <ErrorBanner error={new Error(`Reader report: ${state.error.message}`)} onRetry={retry} />}
      {state.status === 'ready' && <Report r={state.data} headingId={headingId} />}
    </section>
  )
}

function Report({ r, headingId }) {
  const o = r.agreement?.overall || {}
  const pages = r.pages_read || {}
  const totalPages = (pages.desc || 0) + (pages.ga_table || 0) + (pages.ga_detail || 0)
  const bs = r.agreement?.by_status || {}
  const parsersDisagree = (bs.parser_a_differs || 0) + (bs.parser_b_differs || 0) + (bs.all_differ || 0)
  // when Gemini actually read the pages (a cached rerun keeps the report as it was)
  const readAt = r.calls?.read_at?.last || r.generated_at
  return (
    <>
      <header className="rp-head">
        <p className="rp-kicker">
          Reader C <Badge>advisory</Badge>
        </p>
        <h3 id={headingId} className="rp-title">
          Three readers: two parsers and Gemini reading the PDF pages
        </h3>
        <p className="rp-lede">
          Gemini read {n(totalPages)} pages of the two public filings as PDF documents, on its own, and filled the same fields the
          parsers extract. Every value then went through the pipeline&apos;s own normalizing before the comparison.{' '}
          <strong>Gemini proposes; the pipeline&apos;s checks decide.</strong> The records on the map come from the parsers.
        </p>
      </header>

      <Readers r={r} />

      <div className="rp-hero">
        <p className="rp-hero__fig">
          <span className="rp-hero__num">{n(o.gemini_matches_pipeline)}</span>
          <span className="rp-hero__of">of {n(o.compared)} values agree</span>
          <span className="rp-hero__pct">{pct(o.rate)}</span>
        </p>
        <p className="rp-hero__sub">
          {parsersDisagree === 0
            ? `The parsers never disagree with each other (Table 2 has two, the other pages one), so each of the ${n(r.disagreements?.length)} disagreements is Gemini against the parsers.`
            : `${n(r.disagreements?.length)} disagreements; in ${n(parsersDisagree)} of them the parsers disagree with each other.`}{' '}
          Gemini returned {n(r.rows?.compared_records)} of the{' '}
          {n((r.rows?.compared_records || 0) + (r.rows?.gemini_missed?.length || 0))} entries on those pages (each a DESC project page,
          a Table 2 row or a Georgia detail page){r.rows?.unread?.length ? `; ${n(r.rows.unread.length)} went unread` : ''}.
        </p>
      </div>

      <FieldTable fields={r.agreement?.fields || []} />
      <Disagreements items={r.disagreements || []} readers={r.readers || {}} />
      <Rescues rs={r.rescues || {}} apply={r.apply || {}} />

      <p className="rp-fine">
        Read {readAt ? new Date(readAt).toLocaleString('en-US', { dateStyle: 'medium', timeStyle: 'short' }) : ''} by{' '}
        {Object.keys(r.models_used || {}).join(', ') || r.model} (structured output, thinking {r.thinking || 'default'}),{' '}
        {n(r.calls?.requests)} requests: DESC {n(pages.desc)} pages one by one, Georgia Table 2 {n(pages.ga_table)} pages one by one,{' '}
        {n(pages.ga_detail)} Georgia detail pages {n(pages.ga_detail_batch)} to a request, and one request per set-aside record. Built
        offline and cached; nothing calls Gemini when this page opens. Rerun with <code>{r.command}</code>. Public filings: the DESC
        2024-2028 project list and the Georgia 2025 IRP Vol. 3 public-disclosure version (redacted costs are never read).
      </p>
    </>
  )
}

function Readers({ r }) {
  const src = Object.fromEntries((r.sources || []).map((s) => [s.id, s]))
  const rows = [
    { id: 'parser_a', name: 'Parser A', how: 'Text lines', where: 'Every page. What the pipeline keeps.' },
    { id: 'parser_b', name: 'Parser B', how: 'Word positions in the table columns', where: 'Georgia Table 2 only.' },
    {
      id: 'gemini',
      name: 'Gemini',
      how: 'The PDF page itself, with a JSON Schema',
      where: `${Object.keys(r.models_used || {})[0] || r.model}. Advisory.`,
    },
  ]
  return (
    <dl className="rp-readers" aria-label="The three readers">
      {rows.map((x) => (
        <div key={x.id} className={`rp-reader rp-reader--${x.id}`}>
          <dt>{x.name}</dt>
          <dd className="rp-reader__how">{x.how}</dd>
          <dd className="rp-reader__where">{x.where}</dd>
        </div>
      ))}
      <p className="rp-readers__src">
        Sources: {src.desc?.title || 'DESC project list'}; {src.ga_irp?.title || 'Georgia 2025 IRP Vol. 3'}.
      </p>
    </dl>
  )
}

const PART_WORDS = { 'desc:page': 'DESC pages', 'ga_irp:table': 'GA Table 2', 'ga_irp:detail': 'GA detail pages' }

function FieldTable({ fields }) {
  return (
    <div className="rp-block">
      <h4 className="rp-h4">Agreement, field by field</h4>
      <div className="rp-scroll" role="region" aria-label="Agreement by field" tabIndex={0}>
        <table className="rp-table">
          <thead>
            <tr>
              <th scope="col">Field</th>
              <th scope="col">Read on</th>
              <th scope="col" className="rp-num">
                Values
              </th>
              <th scope="col">Gemini = the pipeline&apos;s value</th>
              <th scope="col" className="rp-num">
                All three agree (Table 2)
              </th>
            </tr>
          </thead>
          <tbody>
            {fields.map((f) => {
              const off = f.compared - f.gemini_matches_pipeline
              return (
                <tr key={f.field}>
                  <th scope="row">{f.label}</th>
                  <td data-label="Read on" className="rp-muted">
                    {(f.parts || []).map((p) => PART_WORDS[p] || p).join(', ')}
                  </td>
                  <td data-label="Values" className="rp-num">
                    {n(f.compared)}
                  </td>
                  <td data-label="Gemini = the pipeline's value" className="rp-ratecell">
                    <div className="rp-rate">
                      <span className="rp-bar" aria-hidden="true">
                        <span className="rp-bar__fill" style={{ width: `${f.rate ?? 0}%` }} />
                      </span>
                      <span className="rp-rate__pct">{pct(f.rate)}</span>
                      <span className={`rp-rate__off${off ? ' rp-rate__off--some' : ''}`}>{off ? `${n(off)} differ` : 'all agree'}</span>
                    </div>
                  </td>
                  <td data-label="All three agree (Table 2)" className="rp-num">
                    {f.three_way_rate != null ? pct(f.three_way_rate) : <span className="rp-muted">two readers</span>}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}

// the part of Gemini's value that differs from parser A's (longest common prefix/suffix), so '23O' vs '230' is visible
function Marked({ base, text }) {
  const a = String(text ?? '')
  const b = String(base ?? '')
  if (!b || a === b) return a
  let p = 0
  while (p < a.length && p < b.length && a[p] === b[p]) p++
  let s = 0
  while (s < a.length - p && s < b.length - p && a[a.length - 1 - s] === b[b.length - 1 - s]) s++
  const mid = a.slice(p, a.length - s)
  if (!mid || mid.length > a.length * 0.8) return a
  // a look-alike from another alphabet (Gemini once wrote a Cyrillic Т inside RECONDUCTOR) is invisible without this
  const foreign = [...mid].filter((c) => c.charCodeAt(0) > 127)
  return (
    <>
      {a.slice(0, p)}
      <mark className="rp-mark">{mid}</mark>
      {a.slice(a.length - s)}
      {foreign.length > 0 && (
        <span className="rp-foreign">
          {' '}
          ({foreign.map((c) => `U+${c.charCodeAt(0).toString(16).toUpperCase().padStart(4, '0')}`).join(', ')}: not a Latin letter)
        </span>
      )}
    </>
  )
}

function show(field, v, keys) {
  if (v === null || v === undefined || v === '') return field === 'cost_redacted' ? 'left empty' : '–'
  if (field === 'cost_redacted') return v === 'REDACTED' ? 'REDACTED' : `$${Number(v).toLocaleString('en-US')}`
  if (Array.isArray(v)) {
    if (field === 'kv') return v.length ? `${v.join('/')} kV` : 'none'
    return v.length ? v.join(' · ') : 'none'
  }
  if (typeof v === 'object') {
    const ks = keys && keys.length ? keys : Object.keys(v)
    return ks.map((k) => `${k}: ${typeof v[k] === 'number' ? `$${v[k].toLocaleString('en-US')}` : (v[k] ?? '–')}`).join(', ')
  }
  if (field === 'cost_total' && typeof v === 'number') return `$${v.toLocaleString('en-US')}`
  return String(v)
}

function pageLabel(d) {
  return d.source === 'desc' ? `DESC PDF p. ${d.page}` : `GA IRP Vol. 3 p. ${d.page}`
}

function Disagreements({ items, readers }) {
  const [field, setField] = useState('all')
  const [all, setAll] = useState(false)
  const fields = useMemo(() => {
    const m = new Map()
    for (const d of items) m.set(d.field, { field: d.field, label: d.label, count: (m.get(d.field)?.count || 0) + 1 })
    return [...m.values()]
  }, [items])
  const shown = items.filter((d) => field === 'all' || d.field === field)
  const visible = all ? shown : shown.slice(0, FIRST_SHOWN)
  if (!items.length) return <p className="rp-note">No disagreements: the three readers agree on every value.</p>
  return (
    <div className="rp-block">
      <h4 className="rp-h4">The {n(items.length)} disagreements, each with its page</h4>
      <div className="rp-chips" role="group" aria-label="Show disagreements by field">
        <button type="button" className="rp-chip" aria-pressed={field === 'all'} onClick={() => setField('all')}>
          All <span>{n(items.length)}</span>
        </button>
        {fields.map((f) => (
          <button key={f.field} type="button" className="rp-chip" aria-pressed={field === f.field} onClick={() => setField(f.field)}>
            {f.label} <span>{n(f.count)}</span>
          </button>
        ))}
      </div>
      <ul className="rp-dis">
        {visible.map((d, i) => {
          let keys = null
          if (d.field === 'cost_by_year' && d.values.parser_a && d.values.gemini) {
            keys = Object.keys(d.values.parser_a).filter((k) => d.values.parser_a[k] !== d.values.gemini[k])
          }
          const base = typeof d.raw.parser_a === 'string' ? d.raw.parser_a : null
          return (
            <li key={`${d.record}-${d.field}-${d.part}-${i}`} className="rp-dis__item">
              <div className="rp-dis__top">
                <span className="rp-dis__field">{d.label}</span>
                <code className="rp-dis__id">{d.record}</code>
                {d.status !== 'gemini_differs' && <span className="rp-dis__status">{STATUS_WORDS[d.status] || d.status}</span>}
                <a className="rp-page" href={d.page_url} target="_blank" rel="noreferrer">
                  {pageLabel(d)}
                  <span className="rp-sr"> (opens the public filing)</span>
                </a>
              </div>
              <dl className="rp-vals">
                {READER_ORDER.filter((k) => k in d.raw).map((k) => (
                  <div key={k} className={`rp-val${k === 'gemini' ? ' rp-val--gemini' : ''}`}>
                    <dt>{readers[k]?.name || k}</dt>
                    <dd>
                      {k === 'gemini' && base && typeof d.raw.gemini === 'string' ? (
                        <Marked base={base} text={d.raw.gemini} />
                      ) : (
                        show(d.field, d.raw[k], keys)
                      )}
                    </dd>
                  </div>
                ))}
              </dl>
              {d.pipeline_note && <p className="rp-dis__note">The pipeline recorded: {d.pipeline_note}.</p>}
            </li>
          )
        })}
      </ul>
      {shown.length > FIRST_SHOWN && (
        <button type="button" className="rp-more" onClick={() => setAll((v) => !v)} aria-expanded={all}>
          {all ? 'Show fewer' : `Show all ${n(shown.length)}`}
        </button>
      )}
      <p className="rp-fine">Where Gemini&apos;s reading differs from parser A&apos;s, the differing characters are marked.</p>
    </div>
  )
}

function Rescues({ rs, apply }) {
  const items = rs.items || []
  const accepted = items.filter((i) => i.passes)
  const rest = items.filter((i) => !i.passes)
  const gate = apply.sperry_gate || {}
  const after = gate.after || {}
  const lowConf = rs.passing_low_confidence ?? rs.passing_from_page ?? 0
  const applied = apply.applied
  const ends = after.endpoints_within_1km || {}
  return (
    <div className="rp-block">
      <h4 className="rp-h4">Set-aside records: can Gemini place them?</h4>
      <p className="rp-lede">
        For each record the checks set aside, Gemini read that record&apos;s page and proposed the places the work connects or sits
        at. A name must be printed on the page; then it goes through the pipeline&apos;s own locate step and all 16 checks.
      </p>
      <ol className="rp-funnel" aria-label="Rescue proposals, step by step">
        <li>
          <span className="rp-funnel__n">{n(rs.set_aside)}</span> set aside by the checks
        </li>
        <li>
          <span className="rp-funnel__n">{n(rs.proposed)}</span> proposals with names printed on the page
        </li>
        <li>
          <span className="rp-funnel__n">{n(rs.passing)}</span> accepted by every blocking check
        </li>
        <li>
          <span className="rp-funnel__n">{n(rs.passing_from_title)}</span> placed at a place its title names
        </li>
      </ol>
      {lowConf > 0 && (
        <p className="rp-caution">
          {n(lowConf)} of the {n(rs.passing)} accepted are placed at a station only the page&apos;s description names (a line end or a
          connected station), not one in the title. Each is marked &ldquo;in this area&rdquo;, not the work site, and would go on the map
          at low confidence, which halves its score in the ranking of overlaps.
        </p>
      )}
      <ul className="rp-rescues">
        {accepted.map((i) => (
          <Rescue key={i.id} item={i} />
        ))}
      </ul>
      {rest.length > 0 && (
        <details className="rp-rest">
          <summary>The {n(rest.length)} still set aside, and why</summary>
          <ul className="rp-rescues">
            {rest.map((i) => (
              <Rescue key={i.id} item={i} />
            ))}
          </ul>
        </details>
      )}
      <div className="rp-apply">
        <h5>What applying them would change</h5>
        <p>
          <code>--apply</code> would move {n(apply.would_release?.length)} records from set aside onto the map
          {apply.would_release_title_only ? ` (${n(apply.would_release_title_only.length)} with --title-only)` : ''}, then re-run the
          checks over the whole list and refresh the counts. It writes only if Sperry&apos;s worked example stays as it was: their{' '}
          {n(after.projects_matched)} projects matched to the same records
          {gate.rematched?.length ? ` except ${gate.rematched.join(', ')}` : ''}, and {n(ends.within)} of {n(ends.compared)} of their
          endpoints still within 1 km of ours
          {gate.ok ? ' (it holds).' : ' (it does not hold, so nothing would be written).'} The six overlap distances come from
          Sperry&apos;s own coordinates, so no rescue can change them.
        </p>
        <p>
          {applied?.released?.length
            ? `Applied ${applied.at ? new Date(applied.at).toLocaleDateString('en-US', { dateStyle: 'medium' }) : ''}: ${applied.released.join(', ')}. The next build.py run rebuilds the records from the filings alone and drops them.`
            : 'Not applied: the records on the map come from the parsers alone.'}
        </p>
      </div>
    </div>
  )
}

function Rescue({ item }) {
  const t = item.trials?.[item.chosen ?? 0]
  const blocking = (t?.checks || []).filter((c) => c.blocking)
  const failed = blocking.filter((c) => c.status === 'fail')
  const warned = (t?.checks || []).filter((c) => c.status === 'warn')
  const eps = item.proposal?.endpoints || []
  return (
    <li className={`rp-rescue${item.passes ? ' rp-rescue--ok' : ''}`}>
      <div className="rp-dis__top">
        <code className="rp-dis__id">{item.id}</code>
        <span className="rp-rescue__name">{item.name}</span>
        <a className="rp-page" href={item.page_url} target="_blank" rel="noreferrer">
          {item.source === 'desc' ? `DESC PDF p. ${item.page}` : `GA IRP Vol. 3 p. ${item.page}`}
          <span className="rp-sr"> (opens the public filing)</span>
        </a>
      </div>
      <p className="rp-rescue__line">
        <span className="rp-rescue__lab">Set aside, failing:</span> {(item.reasons_before || []).map((x) => x.split(':')[0]).join('; ')}
      </p>
      {eps.length > 0 && (
        <p className="rp-rescue__line">
          <span className="rp-rescue__lab">Gemini:</span>{' '}
          {eps.map((e, k) => (
            <span key={k} className="rp-quote">
              {e.name}
              {e.quote && e.quote !== e.name ? <q>{e.quote}</q> : null}
            </span>
          ))}
        </p>
      )}
      {t && (
        <ul className="rp-checks" aria-label="The pipeline's blocking checks on the proposal">
          {failed.map((c) => (
            <li key={c.id} className="rp-check rp-check--fail" title={c.detail}>
              <span aria-hidden="true">✕</span> {c.label}
              <span className="rp-sr">: failed</span>
            </li>
          ))}
          <li className="rp-check" title={blocking.filter((c) => c.status !== 'fail').map((c) => c.label).join('; ')}>
            <span aria-hidden="true">✓</span>{' '}
            {failed.length ? `the other ${n(blocking.length - failed.length)} blocking checks pass` : `all ${n(blocking.length)} blocking checks pass`}
          </li>
          {warned.map((c) => (
            <li key={c.id} className="rp-check rp-check--warn" title={c.detail}>
              {c.label}: no (a warning, not blocking)
            </li>
          ))}
        </ul>
      )}
      <p className={`rp-verdict${item.passes ? ' rp-verdict--ok' : ''}`} title={item.caution || undefined}>
        {item.verdict}
        {item.passes ? '.' : ''}
      </p>
    </li>
  )
}
