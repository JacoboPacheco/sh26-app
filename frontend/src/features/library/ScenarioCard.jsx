// One saved scenario in the Library: its verdict, the case as a sentence, three numbers the backend
// computed when it was saved, and what to do with it. Versions sit under their original.
import { useEffect, useState } from 'react'
import { fmt } from '../../geo'
import { Button, ErrorBanner } from '../../ui'
import { deleteScenario } from './libraryApi'
import { VERDICT, appHref, compact, regionName, verdictTone } from './libraryCase'
import { ShareLink } from './ShareButton'

function Verdict({ r }) {
  if (!r) return <span className="lib-verdict lib-verdict--idle">Not measured yet</span>
  return <span className={`lib-verdict lib-verdict--${verdictTone(r.verdict)}`}>{VERDICT[r.verdict] || r.verdict}</span>
}

// Why the number is what it is, in one line — only when there's something to explain.
function why(r) {
  if (!r || !r.campuses) return null
  if (r.firm && r.firm_held) return `Firm service: the campus stayed on; ${fmt(r.shed_mw || 0)} MW of other customers were cut instead.`
  if (r.firm && r.firm_held === false) return 'Firm service could not hold: the campus’s own lines still tripped.'
  // The cliff: past the site's room the campus's own connection trips first and cuts the campus off,
  // so the size stops mattering — a bigger campus blacks out about the same people, not more.
  if (r.site_cut_off && r.campuses === 1 && r.headroom_mw != null)
    return `The campus’s own lines tripped and cut it off. Past this site’s room (${fmt(r.headroom_mw)} MW), a bigger campus blacks out about the same people, not more.`
  if (r.site_cut_off) return 'The campus’s own lines tripped and cut it off, so a bigger campus often blacks out about the same people.'
  if (r.verdict === 'holds' && r.campuses === 1 && r.headroom_mw != null) return `This site has room for ${fmt(r.headroom_mw)} MW before the first line overloads.`
  return null
}

function Numbers({ r }) {
  if (!r) return <p className="lib-fine">Open it to solve it on the synthetic model.</p>
  const worst = r.people_peak ?? r.people ?? 0
  return (
    <dl className="lib-nums">
      <div title={`${fmt(worst)} at the worst, ${fmt(r.people || 0)} at the end (estimates)`}>
        <dt>People without power at the worst (estimate)</dt>
        <dd>{compact(worst)}</dd>
      </div>
      <div>
        <dt>Lines over limit</dt>
        <dd>{fmt(r.overloaded ?? 0)}</dd>
      </div>
      <div>
        <dt>Cascade steps</dt>
        <dd>{fmt(r.steps ?? 0)}</dd>
      </div>
    </dl>
  )
}

// Delete with a second click to confirm (the library is shared by everyone at the demo).
function DeleteButton({ sc, onDeleted }) {
  const [armed, setArmed] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  useEffect(() => {
    if (!armed) return undefined
    const t = setTimeout(() => setArmed(false), 5000)
    return () => clearTimeout(t)
  }, [armed])
  const go = async () => {
    setBusy(true)
    setError(null)
    try {
      await deleteScenario(sc.id)
      onDeleted?.()
    } catch (err) {
      if (/not found/i.test(err.message)) onDeleted?.()
      else setError(err)
    } finally {
      setBusy(false)
    }
  }
  return (
    <>
      {armed ? (
        <Button variant="danger" busy={busy} onClick={go} aria-label={`Confirm: delete ${sc.name}`}>
          Delete for everyone?
        </Button>
      ) : (
        <Button variant="danger" onClick={() => setArmed(true)} aria-label={`Delete ${sc.name}`}>
          Delete
        </Button>
      )}
      <ErrorBanner error={error} />
    </>
  )
}

function Actions({ sc, onOpen, onChanged, picked, onPick }) {
  return (
    <div className="lib-actions">
      <Button onClick={() => onOpen(sc)} aria-label={`Open ${sc.name}`}>
        Open
      </Button>
      <a className="btn btn--secondary" href={appHref(`/brief/${sc.id}`)} aria-label={`Brief: ${sc.name}`}>
        Brief
      </a>
      <ShareLink scenario={sc} onChanged={onChanged} />
      {onPick && (
        <label className="lib-check">
          <input type="checkbox" checked={picked} onChange={() => onPick(sc.id)} />
          Compare
        </label>
      )}
      {!sc.example && <DeleteButton sc={sc} onDeleted={onChanged} />}
    </div>
  )
}

export function ScenarioCard({ sc, versions = [], onOpen, onChanged, picks = [], onPick }) {
  const r = sc.result
  const note = String(sc.note || '')
    .replace('(demo scenario)', '')
    .trim()
  const line = why(r)
  const areas = r?.areas_top?.length ? r.areas_top.slice(0, 3).map((a) => a.area) : []
  return (
    <article className={`lib-card lib-card--${verdictTone(r?.verdict)}`} aria-labelledby={`sc-${sc.id}`}>
      <div className="lib-card__top">
        <Verdict r={r} />
        <span className="lib-where">{regionName(sc.region)}</span>
      </div>
      <h3 className="lib-card__name" id={`sc-${sc.id}`}>
        {sc.name}
      </h3>
      <p className="lib-sentence">{r?.sentence || note}</p>
      {r?.sentence && note && <p className="lib-note">{note}</p>}
      <Numbers r={r} />
      {areas.length > 0 && (
        <p className="lib-fine">
          Hit hardest: {areas.join(', ')}
          {r.areas_top.length > 3 ? ` and ${r.areas_top.length - 3} more` : ''}
        </p>
      )}
      {line && <p className="lib-why">{line}</p>}
      <Actions sc={sc} onOpen={onOpen} onChanged={onChanged} picked={picks.includes(sc.id)} onPick={onPick} />
      {versions.length > 0 && (
        <section className="lib-versions" aria-label={`Versions of ${sc.name}`}>
          <h4 className="lib-versions__h">
            {versions.length} {versions.length === 1 ? 'version' : 'versions'}
          </h4>
          <ol className="lib-versions__list">
            {versions.map((v) => (
              <li key={v.id} className="lib-version">
                <div className="lib-version__main">
                  <p className="lib-version__name">
                    <span className="lib-vnum">v{v.version || '?'}</span> {v.name}
                  </p>
                  <p className="lib-fine">
                    <Verdict r={v.result} /> {v.result ? `${compact(v.result.people_peak ?? v.result.people)} people at the worst (estimate) · ${fmt(v.result.steps)} steps` : ''}
                  </p>
                  {v.result?.sentence && <p className="lib-fine">{v.result.sentence}</p>}
                </div>
                <Actions sc={v} onOpen={onOpen} onChanged={onChanged} picked={picks.includes(v.id)} onPick={onPick} />
              </li>
            ))}
          </ol>
        </section>
      )}
    </article>
  )
}
