import { useCallback, useEffect, useRef, useState } from 'react'
import { useOverload } from '../../store'
import { Button, ErrorBanner, Loading } from '../../ui'
import ReviewStage from '../briefing/ReviewStage'
import { getReport, notLive, prefetchBriefing } from '../briefing/briefingApi'
import { bodyFor } from '../briefing/stage'
import './bulletin.css'

// The review card (it replaces the old three-sentence bulletin): once a cascade has played out, the
// incident's one-line headline (deterministic, from the engine) and the way into the review stage —
// "Review the incident", "Play briefing" (opens it narrating), the full written briefing, and Ask.
// The briefing is fetched the moment the cascade lands, so it is ready by the time the replay ends.
// Mounted by shell/ImpactPanel.jsx under its old name.

export default function ReviewCard() {
  const { cascade, step, caseBody, cascadeBody } = useOverload()
  const n = cascade?.steps?.length || 0
  const done = !!cascade && step >= n
  const [res, setRes] = useState({ cascade: null, body: null, report: null, error: null })
  const [stage, setStage] = useState(null) // {body, autoPlay, startView, startAsk} while the stage is open
  const caseRef = useRef({ caseBody, cascadeBody })
  useEffect(() => {
    caseRef.current = { caseBody, cascadeBody }
  }, [caseBody, cascadeBody])

  const request = useCallback((c) => {
    const body = bodyFor(c, caseRef.current.caseBody, caseRef.current.cascadeBody)
    setRes({ cascade: c, body, report: null, error: null })
    prefetchBriefing(body) // the deck too, template then Gemini
    getReport(body)
      .then((report) => setRes((r) => (r.cascade === c ? { ...r, report } : r)))
      .catch((error) => setRes((r) => (r.cascade === c ? { ...r, error } : r)))
  }, [])

  // one briefing per cascade, asked for as soon as it lands
  useEffect(() => {
    if (cascade && res.cascade !== cascade) request(cascade)
  }, [cascade, res.cascade, request])

  const mine = res.cascade === cascade
  const open = (opts) => setStage({ body: res.body, ...opts })
  // the engine isn't there (not deployed yet): no card rather than an error after every cascade
  const hidden = !mine || !res.body || (res.error && notLive(res.error))
  const stageEl = stage && <ReviewStage {...stage} onClose={() => setStage(null)} />
  if (!done || hidden) return stageEl || null

  return (
    <>
      <section className="review-card" aria-label="Incident briefing">
        <div className="review-card__head">
          <span className="review-card__sim">SIMULATION</span>
          <h2 className="review-card__title">Incident briefing</h2>
        </div>
        {res.error ? (
          <ErrorBanner error={res.error} onRetry={() => request(cascade)} />
        ) : res.report ? (
          <p className="review-card__headline">{res.report.headline?.text}</p>
        ) : (
          <Loading label="Preparing the briefing…" />
        )}
        <div className="review-card__actions">
          <Button onClick={() => open({})}>Review the incident</Button>
          <Button variant="secondary" onClick={() => open({ autoPlay: true })}>
            Play briefing
          </Button>
        </div>
        <div className="review-card__links">
          <button type="button" className="review-card__link" onClick={() => open({ startView: 'document' })}>
            Read the full briefing
          </button>
          <button type="button" className="review-card__link" onClick={() => open({ startAsk: true })}>
            Ask about it
          </button>
        </div>
        <p className="review-card__note">A simulation on a synthetic grid model · people are estimates</p>
      </section>
      {stageEl}
    </>
  )
}
