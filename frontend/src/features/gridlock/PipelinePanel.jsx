import { useEffect, useMemo, useState } from 'react'
import { Button, EmptyState, ErrorBanner, Loading } from '../../ui'
import { CheckIcon } from './DetailCard'
import { useGridlock } from './context'
import PipelineProof from './PipelineProof'
import ReaderProof from './ReaderProof'
import { fmtBuiltAt, fmtInt, sourceLink, toneOf, utilityName } from './format'
import './gridlock.css'

const DEFAULT_COMMAND = 'backend/venv/Scripts/python backend/demo/gridlock/build.py'

// The pipeline, for the judges: every stage with its counts, every check with pass/warn/fail,
// the quarantined records with their reasons, the Sperry reproduction, the sources and the
// one command that rebuilds it all.
export default function PipelinePanel() {
  const g = useGridlock()
  const report = g.summary?.report || {}
  // opened from a funnel node: bring that stage into view (its heading takes focus)
  const focusId = g.pipeFocus
  useEffect(() => {
    if (!focusId) return undefined
    const t = setTimeout(() => {
      const el = document.getElementById(focusId)
      if (!el) return
      el.setAttribute('tabindex', '-1')
      el.scrollIntoView({ block: 'start' })
      el.focus({ preventScroll: true })
    }, 30)
    return () => clearTimeout(t)
  }, [focusId])
  return (
    <div className="gl-pipe">
      <p className="gl-lede">
        Two PDFs in, a ranked list out. Every record keeps its page and raw text, every location its OpenStreetMap feature and a
        confidence, and records that fail a check are set aside with the reason instead of dropped.
      </p>
      <SperryCheck />
      <Stages report={report} />
      <Counts />
      <Checks checks={report.checks} />
      {/* the checks under fire: bad records injected into real ones, caught or missed; the tables in Sperry's own format */}
      <PipelineProof params={g.params} />
      {/* reader C: Gemini reads the same PDF pages; the two parsers and the checks decide (advisory) */}
      <ReaderProof />
      <Extraction x={report.extraction} />
      <Quarantine />
      <Sources context={report.context_sources} />
      <Rebuild report={report} />
    </div>
  )
}

function SperryCheck() {
  const g = useGridlock()
  const s = g.sperry
  if (s.status === 'loading') return <Loading label="Checking against Sperry's worked example…" />
  if (s.status === 'error') return <ErrorBanner error={new Error(`Sperry check: ${s.error.message}`)} onRetry={g.loadSperry} />
  const d = s.data || {}
  const rows = d.rows || []
  const ok = rows.filter((r) => r.ok).length
  const mi2 = (v) => (v == null ? '–' : `${v.toFixed(2)} mi`)
  const samePairs = Array.isArray(d.extra) && Array.isArray(d.missing) && !d.extra.length && !d.missing.length
  return (
    <section className={`gl-sperry${s.data?.all_ok ? ' gl-sperry--ok' : ' gl-sperry--bad'}`} aria-labelledby="gl-sperry-h">
      <div className="gl-sperry__head">
        <CheckIcon status={s.data?.all_ok ? 'pass' : 'fail'} />
        <h3 id="gl-sperry-h">
          Reproduces Sperry&apos;s worked example: {ok} of {rows.length}
        </h3>
      </div>
      <p className="gl-fine">
        Their method (centers, haversine, days between in-service dates), run live on their projects
        {d.pairs_compared != null ? `: ${d.pairs_compared} cross-utility pairs compared, ${d.flagged} under 25 miles` : ''}
        {samePairs ? ', the same pairs as their sheet.' : '.'}
      </p>
      {d.found_in_filings && (
        <p className="gl-fine">
          <strong className="gl-strong">
            {d.found_in_filings.flagged} of {d.found_in_filings.of} of their pairs
          </strong>{' '}
          are also flagged when the full filings are compared ({d.found_in_filings.settings}), found from the PDFs rather than their sheet.
        </p>
      )}
      {d.extra?.length > 0 && <p className="gl-fine gl-n--fail">Flagged here but not in their sheet: {d.extra.join(', ')}</p>}
      {d.missing?.length > 0 && <p className="gl-fine gl-n--fail">In their sheet but not flagged here: {d.missing.join(', ')}</p>}
      {d.data_quality?.length > 0 && (
        <div className="gl-dq">
          <span className="gl-fine">Caught in their sheet and cleaned:</span>
          <ul>
            {d.data_quality.map((n) => (
              <li key={n}>{n}</li>
            ))}
          </ul>
        </div>
      )}
      <details className="gl-details">
        <summary>Compare row by row</summary>
        <table className="gl-table gl-table--num">
          <thead>
            <tr>
              <th scope="col">Pair</th>
              <th scope="col">Sperry</th>
              <th scope="col">Ours</th>
              <th scope="col">Days</th>
              <th scope="col">
                <span className="gl-sr">Match</span>
              </th>
              <th scope="col">Our rank</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.overlap_id}>
                <th scope="row">{r.overlap_id}</th>
                <td>{mi2(r.expected_mi)}</td>
                <td>{mi2(r.ours_mi)}</td>
                <td>
                  {fmtInt(r.ours_days)}
                  {r.expected_days !== r.ours_days && <span className="gl-fine"> (theirs {fmtInt(r.expected_days)})</span>}
                </td>
                <td>
                  <CheckIcon status={r.ok ? 'pass' : 'fail'} />
                </td>
                <td>{r.ours?.rank ? <SperryPairLink ours={r.ours} /> : '–'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </details>
    </section>
  )
}

// Their pair, found in our comparison of the full filings: click to open it on the map.
function SperryPairLink({ ours }) {
  const g = useGridlock()
  const o = g.overlaps.find((x) => x.id === ours.id)
  if (!o) return `#${ours.rank}`
  return (
    <button
      type="button"
      className="gl-link"
      onClick={() => g.openOverlap(o)}
      title={ours.shared_station ? `Same station: ${ours.shared_station}` : `${ours.tier_label}, ${ours.distance_km} km`}
    >
      #{ours.rank}
    </button>
  )
}

const STAGE_NAMES = {
  load: 'Loaded',
  selftest: 'Self-test',
  extract: 'Extracted',
  normalize: 'Cleaned',
  locate: 'Located',
  checks: 'Passed checks',
  quarantine: 'Set aside',
  compare: 'Compared',
  flag: 'Flagged',
}
// 'extract_desc' -> 'Extracted' (the engine's stage ids carry a suffix per source)
const stageName = (id = '') => STAGE_NAMES[id] || STAGE_NAMES[Object.keys(STAGE_NAMES).find((k) => id.startsWith(`${k}_`))] || null
const cap = (t = '') => t.charAt(0).toUpperCase() + t.slice(1)
const fmtMs = (ms) => (ms == null ? '' : ms < 1000 ? `${Math.round(ms)} ms` : `${(ms / 1000).toFixed(1)} s`)

// The engine's stages as a flow, then the three that follow from them: records set aside, and
// (for the current settings) pairs compared and pairs flagged. Each bar is the share a stage kept.
function Stages({ report }) {
  const g = useGridlock()
  const counts = g.summary?.counts || {}
  // located, by confidence: the engine's per-utility counts when it gives them, else the loaded projects
  const conf = useMemo(() => {
    const c = { high: 0, medium: 0, low: 0 }
    const rows = Object.values(g.summary?.counts || {})
    if (rows.some((x) => x.by_confidence)) {
      for (const x of rows) for (const k of Object.keys(c)) c[k] += x.by_confidence?.[k] || 0
    } else for (const p of g.projects.list || []) if (c[p.confidence] != null) c[p.confidence] += 1
    return c
  }, [g.summary, g.projects.list])
  const stages = [...(report.stages || [])]
  const setAside = Object.values(counts).reduce((n, c) => n + (c.quarantined || 0), 0)
  const extracted = Object.values(counts).reduce((n, c) => n + (c.extracted || 0), 0)
  if (setAside && !stages.some((s) => /quarant/i.test(s.id || ''))) {
    stages.push({ id: 'quarantine', label: 'failed a blocking check; kept with the reason', in: extracted || null, out: setAside })
  }
  if (g.ov.status === 'ready' || g.ov.status === 'refreshing') {
    stages.push({ id: 'compare', label: 'cross-state pairs, current settings', out: g.ov.total_pairs, live: true })
    stages.push({ id: 'flag', label: `within ${g.params.max_km} km`, in: g.ov.total_pairs, out: g.ov.flagged, live: true })
  }
  if (!stages.length) return <EmptyState title="No stage report in this build" />
  return (
    <section className="gl-sec" aria-labelledby="gl-stages-h">
      <h3 id="gl-stages-h">Stages</h3>
      <ol className="gl-flow">
        {stages.map((s, i) => {
          const located = s.id === 'locate' || (stages.length <= 3 && s.id === 'load')
          const quarantine = /quarant/i.test(s.id || '')
          const share = s.in ? Math.min(1, (s.out ?? 0) / s.in) : 1
          const known = stageName(s.id)
          const name = known || cap(s.label || s.id)
          const detail = [known ? s.label : null, s.in != null && s.in !== s.out ? `${fmtInt(s.out)} of ${fmtInt(s.in)}` : null, fmtMs(s.ms)]
            .filter(Boolean)
            .join(' · ')
          return (
            <li key={`${s.id}-${i}`} className={`gl-stage${quarantine ? ' gl-stage--q' : ''}${s.live ? ' gl-stage--live' : ''}`}>
              <div className="gl-stage__row">
                <span className="gl-stage__label">{name}</span>
                <strong className="gl-stage__n">{fmtInt(s.out)}</strong>
              </div>
              <div className="gl-stage__bar" aria-hidden="true">
                <span style={{ width: `${Math.max(s.out ? 1.5 : 0, share * 100)}%` }} />
              </div>
              {detail && <span className="gl-fine">{detail}</span>}
              {s.note && <span className="gl-fine gl-stage__note">{s.note}</span>}
              {located && (
                <span className="gl-stage__conf">
                  <span className="gl-conf gl-conf-b--high">{conf.high} high</span>
                  <span className="gl-conf gl-conf-b--medium">{conf.medium} medium</span>
                  <span className="gl-conf gl-conf-b--low">{conf.low} low</span>
                </span>
              )}
              {quarantine && s.out > 0 && (
                <button
                  type="button"
                  className="gl-link"
                  onClick={() => document.getElementById('gl-quarantine')?.scrollIntoView({ behavior: 'smooth', block: 'start' })}
                >
                  See why
                </button>
              )}
            </li>
          )
        })}
      </ol>
    </section>
  )
}

function Counts() {
  const g = useGridlock()
  const counts = g.summary?.counts || {}
  const rows = Object.entries(counts)
  if (!rows.length) return null
  return (
    <section className="gl-sec" aria-labelledby="gl-counts-h">
      <h3 id="gl-counts-h">By utility</h3>
      <table className="gl-table gl-table--num">
        <thead>
          <tr>
            <th scope="col">Utility</th>
            <th scope="col">Extracted</th>
            <th scope="col">Located</th>
            <th scope="col">Set aside</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(([u, c]) => (
            <tr key={u}>
              <th scope="row" title={c.name || u}>
                <span className={`gl-swatch gl-swatch--${toneOf(u)}`} aria-hidden="true" />
                {u}
              </th>
              <td>{fmtInt(c.extracted)}</td>
              <td>
                {fmtInt(c.located)}
                {c.by_confidence && (
                  <span className="gl-confbar" aria-label={`${c.by_confidence.high} high, ${c.by_confidence.medium} medium, ${c.by_confidence.low} low confidence`} role="img">
                    {['high', 'medium', 'low'].map((k) => (
                      <span key={k} className={`gl-confbar--${k}`} style={{ flexGrow: c.by_confidence[k] || 0 }} />
                    ))}
                  </span>
                )}
              </td>
              <td>{fmtInt(c.quarantined)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="gl-fine">The bar under each located count: high, medium and low location confidence.</p>
    </section>
  )
}

function Checks({ checks }) {
  if (!checks?.length) return null
  return (
    <section className="gl-sec" aria-labelledby="gl-checks-h">
      <h3 id="gl-checks-h">Checks ({checks.length})</h3>
      <p className="gl-fine">A blocking check sets the record aside when it fails; the others keep it with a warning.</p>
      <ul className="gl-checks">
        {checks.map((c, i) => {
          const total = Math.max(1, (c.passed || 0) + (c.warned || 0) + (c.failed || 0))
          return (
            <li key={`${c.id || c.label}-${i}`}>
              <div className="gl-checks__row">
                <span>
                  {c.label || c.id}
                  {c.blocking && <span className="gl-block">blocking</span>}
                </span>
                <span className="gl-checks__n">
                  <span>{fmtInt(c.passed || 0)} passed</span>
                  {c.warned > 0 && <span className="gl-n--warn">{fmtInt(c.warned)} warned</span>}
                  {c.failed > 0 && <span className="gl-n--fail">{fmtInt(c.failed)} failed</span>}
                </span>
              </div>
              <div className="gl-checks__bar" aria-hidden="true">
                <span className="gl-b--pass" style={{ width: `${((c.passed || 0) / total) * 100}%` }} />
                <span className="gl-b--warn" style={{ width: `${((c.warned || 0) / total) * 100}%` }} />
                <span className="gl-b--fail" style={{ width: `${((c.failed || 0) / total) * 100}%` }} />
              </div>
            </li>
          )
        })}
      </ul>
    </section>
  )
}

// What the extractors cross-checked (Georgia's table read two ways, table vs detail page titles)
function Extraction({ x }) {
  if (!x || typeof x !== 'object') return null
  const diffs = x.ga_title_differences || []
  const facts = [
    x.desc_pages != null && `DESC: ${fmtInt(x.desc_projects ?? x.desc_pages)} projects from ${fmtInt(x.desc_pages)} pages, one per page`,
    x.ga_rows_by_lines != null &&
      `Georgia's Table 2 read two ways (by text lines and by column position): ${fmtInt(x.ga_parsers_agree)} of ${fmtInt(x.ga_rows_by_lines)} rows agree`,
    x.ga_table_pages?.length && `Table 2 spans pages ${x.ga_table_pages[0]}–${x.ga_table_pages[x.ga_table_pages.length - 1]}`,
    x.ga_detail_pages != null && `${fmtInt(x.ga_detail_pages)} detail pages matched; ${fmtInt(x.ga_titles_identical)} titles identical to the table`,
  ].filter(Boolean)
  if (!facts.length && !diffs.length) return null
  return (
    <section className="gl-sec" aria-labelledby="gl-x-h">
      <h3 id="gl-x-h">Extraction cross-checks</h3>
      <ul className="gl-reasons">
        {facts.map((f) => (
          <li key={f}>{f}</li>
        ))}
      </ul>
      {diffs.length > 0 && (
        <details className="gl-details">
          <summary>Titles that differ between the table and the detail page ({diffs.length})</summary>
          <ul>
            {diffs.map((d) => (
              <li key={d.teams_no}>
                <strong>{d.teams_no}</strong> table &ldquo;{d.table}&rdquo; vs page {d.detail_page} &ldquo;{d.detail}&rdquo;
              </li>
            ))}
          </ul>
        </details>
      )}
    </section>
  )
}

// The records set aside, each with its reasons, page and raw text. `check`: only those that failed that check (its
// label, which each reason starts with); `heading`: false where the page already names the list (the funnel's view).
export function Quarantine({ check = null, heading = true, pageSize = 8 }) {
  const g = useGridlock()
  const [all, setAll] = useState(false)
  const every = g.projects.quarantine || []
  const q = check ? every.filter((r) => (r.reasons || []).some((x) => String(x).startsWith(`${check}:`) || x === check)) : every
  const sources = g.summary?.sources || []
  if (g.projects.status === 'loading') return <Loading label="Loading records…" />
  if (g.projects.status === 'error') return <ErrorBanner error={g.projects.error} onRetry={g.loadProjects} />
  const shown = all ? q : q.slice(0, pageSize)
  return (
    <section className="gl-sec" id={heading ? 'gl-quarantine' : undefined} aria-labelledby={heading ? 'gl-q-h' : undefined} aria-label={heading ? undefined : 'Set-aside records'}>
      {heading && <h3 id="gl-q-h">Set aside ({fmtInt(q.length)})</h3>}
      {!q.length ? (
        <p className="muted">Every extracted record passed.</p>
      ) : (
        <>
          {heading && <p className="gl-fine">Kept out of the comparison, with the reason, so nothing disappears silently.</p>}
          <ul className="gl-qlist">
            {shown.map((r, i) => {
              const src = sources.find((s) => s.id === r.provenance?.source || s.file === r.provenance?.source)
              const link = sourceLink(src, r.provenance?.page)
              return (
                <li key={r.id || i}>
                  <details className="gl-q">
                    <summary>
                      <span className={`gl-swatch gl-swatch--${toneOf(r.utility)}`} aria-hidden="true" />
                      <span className="gl-q__name">{r.name || r.id}</span>
                      <span className="gl-q__why">{r.reasons?.[0]}</span>
                    </summary>
                    <div className="gl-q__body">
                      <span className="gl-fine">
                        {utilityName(r.utility)}
                        {r.id ? ` · ${r.id}` : ''}
                      </span>
                      <ul className="gl-reasons">
                        {(r.reasons || []).map((x) => (
                          <li key={x}>{x}</li>
                        ))}
                      </ul>
                      {r.provenance && (
                        <>
                          <span className="gl-fine">
                            {src?.title || r.provenance.source}
                            {r.provenance.page ? `, page ${r.provenance.page}` : ''}
                            {link && (
                              <>
                                {' · '}
                                <a href={link.url} target="_blank" rel="noreferrer">
                                  {link.label}
                                </a>
                              </>
                            )}
                          </span>
                          {r.provenance.text && <pre className="gl-raw">{r.provenance.text}</pre>}
                        </>
                      )}
                    </div>
                  </details>
                </li>
              )
            })}
          </ul>
          {q.length > pageSize && (
            <Button variant="secondary" onClick={() => setAll((v) => !v)}>
              {all ? 'Show fewer' : `Show all ${q.length}`}
            </Button>
          )}
        </>
      )}
    </section>
  )
}

function Sources({ context }) {
  const g = useGridlock()
  const sources = g.summary?.sources || []
  if (!sources.length && !context?.length) return null
  return (
    <section className="gl-sec" aria-labelledby="gl-src-h">
      <h3 id="gl-src-h">Sources</h3>
      <ul className="gl-srclist">
        {sources.map((s, i) => (
          <li key={s.id || i}>
            {s.url ? (
              <a href={s.url} target="_blank" rel="noreferrer">
                {s.title}
              </a>
            ) : (
              <strong>{s.title}</strong>
            )}
            {s.publisher && <span className="gl-fine">{s.publisher}</span>}
            <span className="gl-fine">{[s.pages && `${fmtInt(s.pages)} page${s.pages === 1 ? '' : 's'}`, s.file].filter(Boolean).join(' · ')}</span>
            {s.sha256 && <span className="gl-fine gl-mono">sha256 {String(s.sha256).slice(0, 16)}…</span>}
          </li>
        ))}
      </ul>
      <p className="gl-fine">Public filings only. Georgia&apos;s project costs are redacted in the public version and stay that way here.</p>
      {context?.length > 0 && (
        <details className="gl-details">
          <summary>Context and map data ({context.length})</summary>
          <ul>
            {context.map((c, i) => (
              <li key={i}>
                {c.url ? (
                  <a href={c.url} target="_blank" rel="noreferrer">
                    {c.title}
                  </a>
                ) : (
                  c.title
                )}
              </li>
            ))}
          </ul>
        </details>
      )}
    </section>
  )
}

function Rebuild({ report }) {
  const g = useGridlock()
  const [copied, setCopied] = useState(false)
  const cmd = g.summary?.rebuild_command || report.command || DEFAULT_COMMAND
  const copy = () => {
    navigator.clipboard?.writeText(cmd).then(
      () => {
        setCopied(true)
        setTimeout(() => setCopied(false), 1500)
      },
      () => {},
    )
  }
  return (
    <section className="gl-sec" aria-labelledby="gl-rebuild-h">
      <h3 id="gl-rebuild-h">Rebuild</h3>
      <p className="gl-fine">
        {g.summary?.built_at ? `Built ${fmtBuiltAt(g.summary.built_at)}. ` : ''}One command re-reads both PDFs, re-runs every check and
        reports what changed:
      </p>
      <div className="gl-cmd">
        <code>{cmd}</code>
        <button type="button" className="gl-link" onClick={copy}>
          {copied ? 'Copied' : 'Copy'}
        </button>
      </div>
      <Changed changed={report.changed_since_last_run} />
    </section>
  )
}

const CHANGE_LABELS = {
  added: 'Added',
  removed: 'Removed',
  changed: 'Changed',
  moved_to_quarantine: 'Newly set aside',
  released_from_quarantine: 'No longer set aside',
}

function Changed({ changed }) {
  if (changed == null) return <p className="gl-fine">Changes since the last run: none recorded.</p>
  if (typeof changed === 'string') return <p className="gl-fine">Changes since the last run: {changed}</p>
  const lists = Array.isArray(changed)
    ? [['changed', changed]]
    : Object.entries(changed).filter(([, v]) => Array.isArray(v))
  const nonEmpty = lists.filter(([, v]) => v.length)
  if (!Array.isArray(changed) && changed.first_run) return <p className="gl-fine">First build: nothing earlier to compare with yet.</p>
  if (!nonEmpty.length) {
    return (
      <p className="gl-fine">
        Nothing changed since the last run{changed.previous_built_at ? ` (${fmtBuiltAt(changed.previous_built_at)})` : ''}.
      </p>
    )
  }
  return (
    <details className="gl-details">
      <summary>
        Changed since the last run: {nonEmpty.map(([k, v]) => `${v.length} ${(CHANGE_LABELS[k] || k).toLowerCase()}`).join(', ')}
      </summary>
      {nonEmpty.map(([k, v]) => (
        <div key={k} className="gl-dq">
          <span className="gl-fine">{CHANGE_LABELS[k] || k}</span>
          <ul>
            {v.slice(0, 30).map((c, i) => (
              <li key={i}>{typeof c === 'string' ? c : [c.id, c.name, c.fields?.length && `changed: ${c.fields.join(', ')}`, c.field && `${c.field}: ${c.before} to ${c.after}`, c.reason].filter(Boolean).join(' · ') || JSON.stringify(c)}</li>
            ))}
            {v.length > 30 && <li>and {v.length - 30} more</li>}
          </ul>
        </div>
      ))}
    </details>
  )
}
