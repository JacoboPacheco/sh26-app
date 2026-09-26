import { useEffect, useMemo, useState } from 'react'
import { Loading } from '../../ui'
import './briefing.css'
import { getReport } from './briefingApi'
import { VERDICT, num, people } from './text'

// The incident briefing, summarized for a saved scenario's written brief (the app's brief page):
// why it happened, the fixes the engine re-ran with their verdicts, or the proof that none exists.
// Everything comes from the engine's report (POST /api/briefing); nothing renders until it answers.
export default function BriefingSummary({ scenario }) {
  const body = useMemo(() => {
    if (scenario?.case) return scenario.case
    if (scenario?.lat == null) return null
    return { region: scenario.region || 'FL', lat: scenario.lat, lon: scenario.lon, mw: scenario.mw }
  }, [scenario])
  const key = body ? JSON.stringify(body) : ''
  const [s, setS] = useState({ key: '', report: null, failed: false })
  useEffect(() => {
    if (!body) return undefined
    let live = true
    getReport(body)
      .then((report) => live && setS({ key, report, failed: false }))
      .catch(() => live && setS({ key, report: null, failed: true }))
    return () => {
      live = false
    }
  }, [body, key])

  if (!body || (s.key === key && s.failed)) return null
  if (s.key !== key) return <Loading label="Preparing the incident briefing…" />
  const r = s.report
  const fixes = (r.fixes || []).filter((f) => f.verdict !== 'not_checked').slice(0, 4)
  return (
    <section className="rs-summary" aria-labelledby="rs-summary-h">
      <h2 id="rs-summary-h">From the incident briefing</h2>
      {r.root_cause?.sentence && <p>{r.root_cause.sentence}</p>}
      {r.no_fix ? (
        <p className="rs-summary__nofix">
          No fix exists for about {people(r.no_fix.people)} people (estimate): whatever the campus does, and even with unlimited line ratings, they stay cut off.
          Only rebuilding lines brings them back.
        </p>
      ) : (
        fixes.length > 0 && (
          <ul className="rs-summary__fixes">
            {fixes.map((f, i) => (
              <li key={`${f.family}${i}`}>
                <span>{f.action || f.label}</span>
                <span className={`rs-chip rs-chip--${f.verdict}`}>{VERDICT.en[f.verdict] || f.verdict}</span>
              </li>
            ))}
          </ul>
        )
      )}
      {r.recovery?.waves?.length > 0 && (
        <p>
          Rebuilding the first {num(r.recovery.waves[0].lines?.length || r.recovery.waves[0].line_count)} lines brings about{' '}
          {people(r.recovery.waves[0].people_back)} people back (estimate).
        </p>
      )}
      <p className="muted">Each fix was re-run on the synthetic grid model; people are estimates.</p>
    </section>
  )
}
