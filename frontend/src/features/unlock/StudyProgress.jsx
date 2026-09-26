// While a study runs: which step of the study it is on (learn, plan, verify, Gemini), how far along, the sites
// simulated so far and the weak points found so far (the map fills in with them as they come).
import { useEffect, useState } from 'react'
import { fmt } from '../../geo'

const PHASES = [
  ['learn', 'Learn the weak points', 'a campus at every town, the full cascade wherever a line overloads'],
  ['plan', 'Test the cheapest fixes', 'upgrades priced from published figures, cheapest per site first'],
  ['verify', 'Verify with the engine', 'every unlocked site re-run through the full cascade'],
  ['ai', 'Gemini proposes, the engine checks', 'other bundles from the weak points; only verified ones count'],
]

function useSeconds(since) {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    if (!since) return undefined
    const t = setInterval(() => setNow(Date.now()), 500)
    return () => clearInterval(t)
  }, [since])
  return since ? Math.max(0, Math.round((now - since) / 1000)) : 0
}

export default function StudyProgress({ u, where }) {
  const p = u.progress || {}
  const at = Math.max(
    0,
    PHASES.findIndex(([id]) => id === p.phase),
  )
  const frac = p.total ? Math.min(1, p.done / p.total) : 0
  const secs = useSeconds(u.startedAt)
  const sites = u.partial?.sites || []
  const tested = sites.filter((s) => s.hit0 != null).length
  const points = u.partial?.points || []
  return (
    <section className="st-progress" aria-live="polite" aria-labelledby="st-prog-h">
      <h2 className="st-h" id="st-prog-h">
        Studying {where}&apos;s grid
      </h2>
      <p className="st-progress__time">
        {fmt(secs)} s{u.estimate?.seconds ? ` of about ${fmt(u.estimate.seconds)} s` : ''}
        {sites.length > 0 && (
          <>
            {' '}
            · {fmt(tested)} of {fmt(sites.length)} sites simulated
          </>
        )}
      </p>
      <ol className="ul-phases">
        {PHASES.map(([id, label, sub], i) => {
          const st = p.phase === 'queued' || !p.phase ? 'todo' : i < at ? 'done' : i === at ? 'now' : 'todo'
          return (
            <li key={id} className={`ul-phase ul-phase--${st}`}>
              <span className="ul-phase__mark" aria-hidden="true" />
              <span className="ul-phase__text">
                <strong>{label}</strong>
                <span>{sub}</span>
                {st === 'now' && (
                  <span className="ul-bar" aria-hidden="true">
                    <span style={{ transform: `scaleX(${frac})` }} />
                  </span>
                )}
              </span>
            </li>
          )
        })}
      </ol>
      <p className="ul-msg">{p.message || 'Working…'}</p>
      {points.length > 0 && (
        <div className="st-progress__pts">
          <h3 className="st-h">Weak points found so far</h3>
          <ol>
            {points.slice(0, 6).map((pt) => (
              <li key={pt.branch_id}>
                <strong>{pt.short || pt.label.replace(/^the /, '')}</strong>
                <span>{pt.reason}</span>
              </li>
            ))}
          </ol>
        </div>
      )}
    </section>
  )
}
