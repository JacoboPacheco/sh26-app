import { useMemo } from 'react'
import { useGridlock } from './context'
import Gloss from './Gloss'
import { fmtInt, limitMi } from './format'

// The data pipeline as proof, always in view along the top of the map: how many projects were read from the public
// filings, how many passed the checks and were placed on the map, how many pairs came within the distance and how many
// build in the same months; the location accuracy against Sperry's hand-placed points when the engine sends it. The
// button opens the full pipeline (#/plans/pipeline). With a pair's sheet open, only that button stays.

export default function ProofStrip() {
  const g = useGridlock()
  const f = g.summary?.funnel
  const ready = g.ov.status === 'ready' || g.ov.status === 'refreshing'
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
          <strong>{ready ? fmtInt(g.ov.flagged ?? g.overlaps.length) : '…'}</strong>{' '}
          <Gloss tip={`Pairs of one DESC project and one Georgia project whose closest points are within ${limitMi(g.params.max_km)} (Sperry's cutoff is 25 mi, 40.2336 km)${g.ov.total_pairs ? `, of ${fmtInt(g.ov.total_pairs)} pairs compared` : ''}.`}>
            pairs within {limitMi(g.params.max_km)}
          </Gloss>
        </li>
        <li>
          <strong>{ready ? fmtInt(same) : '…'}</strong>{' '}
          <Gloss tip="Both build windows share months that are still ahead or open now: they could be built together as filed.">whose filed windows share months</Gloss>
        </li>
      </ol>
      <div className="bt-proof__row">
        {accText ? (
          <p className="bt-proof__acc">
            <svg viewBox="0 0 16 16" aria-hidden="true">
              <path d="M3.5 8.5l3 3 6-7" />
            </svg>
            <Gloss tip={acc.text ? `${acc.text}. ${acc.method || ''}` : `Our located project ends against the ${fmtInt(accN)} endpoints Sperry placed by hand.`}>
              {accText}
            </Gloss>
          </p>
        ) : (
          <span />
        )}
        <button type="button" className="bt-proof__go" onClick={() => g.openPipeline()}>
          How we built the data
          <svg viewBox="0 0 16 16" aria-hidden="true">
            <path d="M6 3.5L10.5 8 6 12.5" />
          </svg>
        </button>
      </div>
    </nav>
  )
}
