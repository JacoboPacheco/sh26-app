import { Fragment, useMemo, useState } from 'react'
import { Button, ErrorBanner, Loading } from '../../ui'
import { CheckIcon } from './DetailCard'
import { useGridlock } from './context'
import { Quarantine } from './PipelinePanel'
import { KM_PER_MI, displayName, fmtInt, toneOf, utilityShort } from './format'

// The pipeline where the judge looks: one quiet line under the page intro, every number counted by the engine
// (/api/gridlock/summary funnel; the pair counts from /overlaps at the current settings). Each node is a control:
// the filings, rows and checks open the pipeline at that stage, "set aside" opens the records the checks kept out
// (with the reasons), "pairs" opens the settings that decide what is compared, "flagged" is the list itself and
// "top" opens pair 1. Above it, the way to start from Sperry's own worked example.

export default function PipelineFunnel() {
  const g = useGridlock()
  const f = g.summary?.funnel
  if (!f) return null
  const ovReady = g.ov.status === 'ready' || g.ov.status === 'refreshing'
  const top = ovReady ? g.overlaps.find((o) => o.rank === 1) || g.overlaps[0] : null
  const c = ovReady ? g.ov.compared : null
  const mi = g.params.max_km / KM_PER_MI
  const sides = c ? `${fmtInt(c.a_projects)} DESC × ${fmtInt(c.b_projects)} ${sideB(g.pairs)} projects` : null
  // from "passed" to "pairs": the projects that passed but sit out of this comparison (utilities switched off in the
  // settings, or never placed on the map), so 194 = 43 + 104 + 47 reads on the page, not only in a tooltip
  const off = (c?.not_compared || []).filter((x) => x.projects > 0)
  const unplaced = c?.unplaced || 0
  const offText = offLine(off, unplaced)
  const unplacedText = unplaced > 0 ? `${fmtInt(unplaced)} that passed weren't placed on the map` : null
  const outText = [offText, unplacedText].filter(Boolean).join('; ')
  const sourceLine = (f.by_source || [])
    .map((s) => `${s.utility === 'DESC' ? 'DESC' : 'Georgia'}: ${fmtInt(s.rows)} rows from ${fmtInt(s.pages)} pages`)
    .join('; ')
  const failedLine = (f.set_aside_by_check || []).map((x) => `${x.label.toLowerCase()} (${fmtInt(x.records)})`).join('; ')

  const toFilters = () => {
    g.setFiltersOpen(true)
    requestAnimationFrame(() => {
      const el = document.querySelector('.gl-filters > summary')
      el?.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
      el?.focus({ preventScroll: true })
    })
  }
  const toList = () => {
    const el = document.getElementById('gl-ranked-h')
    el?.scrollIntoView({ block: 'start', behavior: 'smooth' })
    el?.focus({ preventScroll: true })
  }

  const nodes = [
    {
      id: 'filings',
      n: fmtInt(f.filings),
      label: f.filings === 1 ? 'filing' : 'filings',
      title: `${fmtInt(f.filings)} public filing${f.filings === 1 ? '' : 's'}${f.pages ? `, ${fmtInt(f.pages)} pages` : ''}: see the sources and each PDF's SHA-256`,
      onClick: () => g.openPipeline('gl-src-h'),
    },
    {
      id: 'rows',
      n: fmtInt(f.extracted),
      label: 'rows',
      title: `${fmtInt(f.extracted)} project rows read from the PDFs${sourceLine ? ` (${sourceLine})` : ''}: see how they were read`,
      onClick: () => g.openPipeline('gl-stages-h'),
    },
    {
      id: 'passed',
      n: fmtInt(f.passed),
      label: 'passed',
      title: `${fmtInt(f.passed)} passed all ${fmtInt(f.blocking_checks)} blocking checks (of ${fmtInt(f.checks)}) and were placed on the map: see the checks`,
      onClick: () => g.openPipeline('gl-checks-h'),
      fork: f.set_aside > 0 && {
        id: 'set_aside',
        text: `−${fmtInt(f.set_aside)} set aside`,
        title: `${fmtInt(f.set_aside)} set aside with the reason, not dropped${failedLine ? `: ${failedLine}` : ''}`,
        onClick: () => g.setTab('setaside'),
      },
    },
    {
      id: 'pairs',
      n: ovReady ? fmtInt(g.ov.total_pairs) : '…',
      label: 'pairs',
      // the two sides, on the page: 4,472 = 43 × 104
      sub: c && c.disjoint ? `${fmtInt(c.a_projects)} × ${fmtInt(c.b_projects)}` : null,
      title: ovReady
        ? `${fmtInt(g.ov.total_pairs)} cross-state pairs compared${sides ? `: ${sides}` : ''}${outText ? `; ${outText}` : ''}. Open the settings`
        : 'Comparing…',
      onClick: toFilters,
    },
    {
      id: 'flagged',
      n: ovReady ? fmtInt(g.ov.flagged) : '…',
      label: 'flagged',
      title: ovReady ? `${fmtInt(g.ov.flagged)} pairs within ${mi.toFixed(mi < 10 ? 1 : 0)} mi, ranked: the list below` : 'Ranking…',
      onClick: toList,
    },
    {
      id: 'top',
      n: top ? '#1' : '–',
      label: 'top',
      title: top ? `Open pair 1: ${displayName(g.byId[top.a]?.name) || top.a} and ${displayName(g.byId[top.b]?.name) || top.b}` : 'No pair flagged',
      onClick: top ? () => g.openDraft(top) : null,
    },
  ]

  return (
    <nav className="gl-funnel" aria-label="The data pipeline, from the filings to this list">
      <div className="gl-funnel__top">
        <span className="gl-funnel__cap">From the filings to this list</span>
        {g.sperry.status !== 'error' && (
          <button type="button" className="gl-funnel__sperry" onClick={g.enterSperry}>
            Start from Sperry&apos;s example <span aria-hidden="true">›</span>
          </button>
        )}
      </div>
      <ol className="gl-funnel__flow">
        {nodes.map((x, i) => (
          <Fragment key={x.id}>
            {i > 0 && (
              <li className="gl-funnel__sep" aria-hidden="true">
                ›
              </li>
            )}
            <li className={x.fork ? 'gl-funnel__fork' : undefined}>
              <button type="button" className="gl-funnel__node" data-node={x.id} title={x.title} aria-label={x.title} onClick={x.onClick || undefined} disabled={!x.onClick}>
                <strong aria-hidden="true">{x.n}</strong>
                <span aria-hidden="true">{x.label}</span>
                {x.sub && (
                  <em className="gl-funnel__sub" aria-hidden="true">
                    {x.sub}
                  </em>
                )}
              </button>
              {x.fork && (
                <button type="button" className="gl-funnel__aside" data-node={x.fork.id} title={x.fork.title} aria-label={x.fork.title} onClick={x.fork.onClick}>
                  {x.fork.text}
                </button>
              )}
            </li>
          </Fragment>
        ))}
      </ol>
      {c && c.disjoint && (
        <p className="gl-funnel__note">
          {fmtInt(g.ov.total_pairs)} = {sides}
          {offText && (
            <>
              ; {offText}{' '}
              <button type="button" className="gl-funnel__notelink" onClick={toFilters}>
                in Filters
              </button>
            </>
          )}
          {unplacedText && <>; {unplacedText}</>}.
        </p>
      )}
    </nav>
  )
}

// the Georgia side of the comparison, as the settings have it
function sideB(pairs) {
  return pairs.length === 1 && pairs[0][1] === 'GPC' ? 'Georgia Power' : 'Georgia'
}

// "the other 47 that passed (GTC 36, MEAG 10, Dalton 1) are switched off": counted by the engine at the current settings
function offLine(off, unplaced) {
  const n = off.reduce((s, x) => s + x.projects, 0)
  if (!n) return null
  const who = off.map((x) => `${utilityShort(x.utility)} ${fmtInt(x.projects)}`).join(', ')
  return `${unplaced ? '' : 'the other '}${fmtInt(n)} that passed (${who}) are switched off`
}

// ------------------------------------------------------------------ the records the checks kept out
export function SetAsideView() {
  const g = useGridlock()
  const f = g.summary?.funnel
  const [check, setCheck] = useState(null)
  if (!f) return null
  const by = f.set_aside_by_check || []
  const cur = by.find((x) => x.id === check) || null
  return (
    <section className="gl-aside" aria-label="Records set aside by the checks">
      <p className="gl-lede">
        {fmtInt(f.set_aside)} of the {fmtInt(f.extracted)} rows read failed a blocking check. They stay out of the comparison with the reason
        recorded, so nothing disappears silently.
      </p>
      {by.length > 0 && (
        <div className="gl-aside__why">
          <span className="gl-fine">Why{by.length > 1 ? ' (a record can fail more than one check)' : ''}</span>
          <div className="gl-tierset__opts" role="group" aria-label="Show the records that failed one check">
            <button type="button" className={`gl-chip${!cur ? ' is-on' : ''}`} aria-pressed={!cur} onClick={() => setCheck(null)}>
              All {fmtInt(f.set_aside)}
            </button>
            {by.map((x) => (
              <button
                key={x.id}
                type="button"
                className={`gl-chip${cur?.id === x.id ? ' is-on' : ''}`}
                aria-pressed={cur?.id === x.id}
                title={`Failed the check "${x.check}"`}
                onClick={() => setCheck(cur?.id === x.id ? null : x.id)}
              >
                {x.label} · {fmtInt(x.records)}
              </button>
            ))}
          </div>
        </div>
      )}
      <Quarantine check={cur?.check || null} heading={false} pageSize={20} />
      <button type="button" className="gl-link" onClick={() => g.openPipeline('gl-checks-h')}>
        Every check, with its pass and warning counts
      </button>
    </section>
  )
}

// ------------------------------------------------------------------ Sperry's worked example, as the start
function tolText(tol) {
  if (!tol) return null
  const mi = tol.mi != null ? `to ${tol.mi} mi` : null
  const days = tol.days === 0 ? 'to the day' : tol.days != null ? `within ${tol.days} days` : null
  return [mi, days].filter(Boolean).join(' and ')
}

export function SperryView() {
  const g = useGridlock()
  const s = g.sperry
  const f = g.summary?.funnel
  // what "Expand" leads to: the comparison at the current settings (DESC x Georgia Power by default: 147 of the 194)
  const cmp = g.ov.status === 'ready' || g.ov.status === 'refreshing' ? g.ov.compared : null
  if (s.status === 'loading') return <Loading label="Checking against Sperry's worked example…" />
  if (s.status === 'error') return <ErrorBanner error={new Error(`Sperry check: ${s.error.message}`)} onRetry={g.loadSperry} />
  const d = s.data || {}
  const rows = d.rows || []
  const ok = rows.filter((r) => r.ok).length
  const same = Array.isArray(d.extra) && Array.isArray(d.missing) && !d.extra.length && !d.missing.length
  const nProjects = d.projects_in_example ?? (d.projects || []).length
  const mapPairs = new Map((g.sperryMap?.overlaps || []).map((o) => [o.sperry, o]))
  const names = Object.fromEntries((d.projects || []).map((p) => [p.sperry_id, p]))
  return (
    <section className="gl-sperrymode" aria-labelledby="gl-sperrymode-h">
      <div className={`gl-sperrymode__verdict${d.all_ok ? ' is-ok' : ' is-bad'}`}>
        <CheckIcon status={d.all_ok ? 'pass' : 'fail'} />
        <div>
          <h2 id="gl-sperrymode-h">
            Reproduced {ok} of {rows.length}
          </h2>
          {tolText(d.tolerance) && <p>{tolText(d.tolerance)}</p>}
        </div>
      </div>
      <p className="gl-fine">
        Their {fmtInt(nProjects)} projects, measured their way (centers, haversine, days between in-service dates)
        {d.pairs_compared != null ? `: ${fmtInt(d.pairs_compared)} cross-utility pairs compared, ${fmtInt(d.flagged)} under 25 miles` : ''}
        {same ? ', the same pairs as their sheet.' : '.'}
      </p>
      {d.extra?.length > 0 && <p className="gl-fine gl-n--fail">Flagged here but not in their sheet: {d.extra.join(', ')}</p>}
      {d.missing?.length > 0 && <p className="gl-fine gl-n--fail">In their sheet but not flagged here: {d.missing.join(', ')}</p>}
      <div className="gl-sperrymode__next">
        <Button onClick={g.expandSperry} data-action="expand">
          Expand to the full filings <span aria-hidden="true">›</span>
        </Button>
        {f && (
          <p className="gl-fine">
            {fmtInt(f.extracted)} rows from {fmtInt(f.filings)} filings, {fmtInt(f.passed)} passed the checks
            {cmp?.disjoint
              ? `, and ${fmtInt(cmp.a_projects)} DESC × ${fmtInt(cmp.b_projects)} ${sideB(g.pairs)} projects are compared: ${fmtInt(g.ov.total_pairs)} pairs`
              : ''}
            . Their {rows.length} stay marked where they rank.
          </p>
        )}
      </div>
      <ol className="gl-sperrymode__rows" aria-label="Their pairs, their numbers against ours">
        {rows.map((r) => {
          const o = mapPairs.get(r.overlap_id)
          const n = String(r.overlap_id).replace(/\D+/g, '')
          const days = r.expected_days === r.ours_days ? `${fmtInt(r.ours_days)} days, same` : `${fmtInt(r.ours_days)} days (theirs ${fmtInt(r.expected_days)})`
          const body = (
            <>
              <span className="gl-sperrymode__n" aria-hidden="true">
                {n}
              </span>
              <span className="gl-sperrymode__body">
                <span className="gl-sperrymode__id">{r.overlap_id}</span>
                {[
                  [r.a, r.a_name],
                  [r.b, r.b_name],
                ].map(([id, name]) => (
                  <span key={id} className="gl-row__proj">
                    <span className={`gl-swatch gl-swatch--${toneOf(names[id]?.utility)}`} aria-hidden="true" />
                    <span className="gl-sr">{utilityShort(names[id]?.utility)}: </span>
                    <span className="gl-row__name">{displayName(name)}</span>
                  </span>
                ))}
                <span className="gl-sperrymode__nums">
                  <span>
                    Sperry {r.expected_mi?.toFixed(2)} mi · ours {r.ours_mi == null ? '–' : `${r.ours_mi.toFixed(2)} mi`}
                  </span>
                  <span>{days}</span>
                </span>
              </span>
              <CheckIcon status={r.ok ? 'pass' : 'fail'} />
            </>
          )
          return (
            <li key={r.overlap_id}>
              {o ? (
                <button
                  type="button"
                  className="gl-sperrymode__row"
                  onClick={() => g.openDraft(o)}
                  onPointerEnter={(e) => e.pointerType !== 'touch' && g.setHover({ kind: 'overlap', id: o.id })}
                  onPointerLeave={() => g.setHover(null)}
                >
                  {body}
                </button>
              ) : (
                <div className="gl-sperrymode__row">{body}</div>
              )}
            </li>
          )
        })}
      </ol>
      {d.data_quality?.length > 0 && (
        <div className="gl-dq">
          <span className="gl-fine">Caught in their sheet and cleaned:</span>
          <ul>
            {d.data_quality.map((x) => (
              <li key={x}>{x}</li>
            ))}
          </ul>
        </div>
      )}
    </section>
  )
}

// ------------------------------------------------------------------ after expanding: where their six rank
export function SperryMarks({ onShow }) {
  const g = useGridlock()
  const six = useMemo(
    () =>
      (g.overlaps || [])
        .filter((o) => o.sperry)
        .sort((a, b) => Number(a.sperry.replace(/\D+/g, '')) - Number(b.sperry.replace(/\D+/g, ''))),
    [g.overlaps],
  )
  const expected = g.sperry.data?.rows?.length || 6
  if (!g.sperryMarks) return null
  const byRank = [...six].sort((a, b) => (a.rank ?? 0) - (b.rank ?? 0))
  return (
    <div className="gl-sperrymarks" role="region" aria-label="Sperry's six pairs in the full filings">
      <div className="gl-sperrymarks__head">
        <p>
          <strong>Sperry&apos;s {expected} pairs</strong>, found again in the full filings
          {six.length < expected ? ` (${six.length} within the current filters)` : ''}
        </p>
        <button type="button" className="gl-close gl-close--sm" onClick={() => g.setSperryMarks(false)} aria-label="Stop marking Sperry's pairs" title="Stop marking">
          ×
        </button>
      </div>
      <ul className="gl-sperrymarks__list">
        {byRank.map((o) => (
          <li key={o.id}>
            <button type="button" className="gl-chip" onClick={() => onShow(o)} title={`Show ${o.sperry} in the list: rank ${o.rank}`}>
              <strong>#{o.displayRank ?? o.rank}</strong> {o.sperry}
            </button>
          </li>
        ))}
      </ul>
    </div>
  )
}
