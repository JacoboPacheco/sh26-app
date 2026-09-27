import { useMemo } from 'react'
import { useGridlock } from './context'
import Gloss from './Gloss'
import { fmtInt, limitMi } from './format'
import { useFaultTotals } from './useFaultTotals'
import { useReaderTotals } from './useReaderTotals'

// The data pipeline as proof, always in view along the top of the map: how many projects were read from the public
// filings, how many passed the checks and were placed on the map, how many pairs came within the distance and how many
// build in the same months; the location accuracy against Sperry's hand-placed points when the engine sends it. The
// button opens the full pipeline (#/plans/pipeline). With a pair's sheet open, only that button stays.

function Tick() {
  return (
    <svg viewBox="0 0 16 16" aria-hidden="true">
      <path d="M3.5 8.5l3 3 6-7" />
    </svg>
  )
}

export default function ProofStrip() {
  const g = useGridlock()
  const fault = useFaultTotals(g.client)
  const reader = useReaderTotals(g.client)
  const f = g.summary?.funnel
  const ready = g.ov.status === 'ready' || g.ov.status === 'refreshing'
  const wait = g.ov.status === 'error' ? '–' : '…' // the pairs failed to load: a dash, not a count that is forever "coming"
  const same = useMemo(() => g.overlaps.filter((o) => o.group === 'together').length, [g.overlaps])
  if (g.conn.status !== 'ready' || !f) return null
  // the sheet's third step covers most of the map, and a project's card sits where the strip would run: step aside
  if ((g.draft && g.pairStep === 'plans') || (!g.draft && g.sel?.kind === 'project')) return null
  const narrow = !!g.draft
  const acc = g.summary?.location_accuracy
  const dv = f.default_view || null
  const rescued = typeof f.rescued === 'object' ? f.rescued?.count : f.rescued
  const filings = (g.summary?.sources || []).map((s) => s.short_title || s.title).filter(Boolean)
  const setAside = f.set_aside ?? (f.extracted != null && f.passed != null ? f.extracted - f.passed : null)
  const placedTip = [
    `${fmtInt(f.placed ?? f.passed)} passed every blocking check and have at least one end placed on OpenStreetMap.`,
    setAside ? `${fmtInt(setAside)} were set aside, each with its reason.` : null,
    dv?.text && !g.allGeorgia ? dv.text : null,
    rescued ? `${fmtInt(rescued)} were placed from a station named in the project's description, at low confidence.` : null,
  ]
    .filter(Boolean)
    .join(' ')
  const accN = acc?.compared ?? acc?.points
  const accIn = acc?.within ?? acc?.within_500m
  const accText = acc && accN ? `${fmtInt(accIn)} of ${fmtInt(accN)} ends within ${fmtInt(acc.within_m ?? 500)} m of Sperry's hand-placed points` : null
  if (narrow) {
    return (
      <nav className="gl bt-proof is-narrow" aria-label="How the data was built" style={{ '--bt-cover': `${g.cover || 0}px` }}>
        <button type="button" className="bt-proof__go" onClick={() => g.openPipeline()}>
          How we built the data
          <span className="bt-proof__sub">
            {' '}
            ({fmtInt(f.extracted)} projects, {fmtInt(f.filings)} filings)
          </span>
          <svg viewBox="0 0 16 16" aria-hidden="true">
            <path d="M6 3.5L10.5 8 6 12.5" />
          </svg>
        </button>
      </nav>
    )
  }
  return (
    <nav className="gl bt-proof" aria-label="How the data was built" style={{ '--bt-cover': `${g.cover || 0}px` }}>
      <ol className="bt-proof__steps">
        <li>
          <strong>{fmtInt(f.extracted)}</strong>{' '}
          <Gloss tip={`Read row by row from ${filings.length ? filings.join('; ') : 'the public filings'}. Georgia's is the public-disclosure copy: redacted fields are not used.`}>
            projects read from {fmtInt(f.filings)} public filings
          </Gloss>
        </li>
        <li>
          <strong>{fmtInt(f.placed ?? f.passed)}</strong> <Gloss tip={placedTip}>checked and placed</Gloss>
        </li>
        <li>
          <strong>{ready ? fmtInt(g.ov.flagged ?? g.overlaps.length) : wait}</strong>{' '}
          <Gloss tip={`Pairs of one DESC project and one Georgia project whose closest points are within ${limitMi(g.params.max_km)} (Sperry's cutoff is 25 mi, 40.2336 km)${g.ov.total_pairs ? `, of ${fmtInt(g.ov.total_pairs)} pairs compared` : ''}.`}>
            pairs within {limitMi(g.params.max_km)}
          </Gloss>
        </li>
        <li>
          <strong>{ready ? fmtInt(same) : wait}</strong>{' '}
          <Gloss tip="Both build windows (as filed, or from a start derived from the in-service date) share months that are still ahead or open now: they could be built together.">whose build windows still share months</Gloss>
        </li>
      </ol>
      <button type="button" className="bt-proof__go bt-proof__go--top" onClick={() => g.openPipeline()}>
        How we built the data
        <svg viewBox="0 0 16 16" aria-hidden="true">
          <path d="M6 3.5L10.5 8 6 12.5" />
        </svg>
      </button>
      <div className="bt-proof__row">
        {/* why it is not a toy: what the checks kept out, how the checks were tried, how the places compare with Sperry's */}
        <ul className="bt-proof__checks" aria-label="How the data was checked">
          {setAside > 0 && (
            <li>
              <Tick />
              <Gloss tip="Records that failed a blocking check are kept out of the comparison and listed with the reason and the PDF page, so nothing disappears silently.">
                {fmtInt(setAside)} set aside, with reasons
              </Gloss>
            </li>
          )}
          {fault && (
            <li>
              <Tick />
              <Gloss tip="Nine kinds of bad data (a wrong date, a place in another state, a duplicate id...) were injected into real records and run through the same checks. The ones that slip through are listed on the pipeline page.">
                fault test: {fmtInt(fault.caught)} of {fmtInt(fault.injected)} injected errors caught
              </Gloss>
            </li>
          )}
          {reader && (
            <li>
              <Tick />
              <Gloss tip="Gemini read the same PDF pages on its own, offline, and filled the same fields the two parsers extract: a third, independent check on what got read. It's advisory — the records on the map come from the parsers — and it's built once, not asked live.">
                a second reader: Gemini agrees on {fmtInt(reader.matches)} of {fmtInt(reader.compared)} values
              </Gloss>
            </li>
          )}
          {accText && (
            <li>
              <Tick />
              <Gloss tip={acc.text ? `${acc.text}. ${acc.method || ''}` : `Our located project ends against the ${fmtInt(accN)} endpoints Sperry placed by hand.`}>
                {accText}
              </Gloss>
            </li>
          )}
        </ul>
      </div>
    </nav>
  )
}
