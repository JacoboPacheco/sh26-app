// FEATURE: "How AI is used" — one panel that says, for every AI feature, what Gemini does, what checks it
// and what runs without it. The rule everywhere: Gemini writes or proposes, the engine or the fact sheet
// checks, and a labeled fallback runs when the key, the quota or the network is missing.
// Props (all optional): label — the link's text; surface — the id of the feature it is opened from
// (GET /api/ai/status → surfaces[].id: 'cost', 'ask', 'planner', …), which the panel marks and scrolls to;
// className — extra classes for the link (placement in a footer).
import { useEffect, useRef, useState } from 'react'
import { ErrorBanner, Loading } from '../../ui'
import { getAiStatus } from './aiApi'
import TrustCard from './TrustCard'
import './ai.css'

export default function HowAiIsUsed({ label = 'How AI is used', surface, className }) {
  const dlg = useRef(null)
  const [open, setOpen] = useState(false)
  const [st, setSt] = useState(null)
  const [err, setErr] = useState(null)
  const [retry, setRetry] = useState(0)

  useEffect(() => {
    if (!open) return undefined
    let live = true
    getAiStatus()
      .then((d) => live && (setSt(d), setErr(null)))
      .catch((e) => live && setErr(e))
    return () => {
      live = false
    }
  }, [open, retry])

  useEffect(() => {
    const d = dlg.current
    if (!d) return
    if (open && !d.open) d.showModal()
    if (!open && d.open) d.close()
  }, [open])

  // opened from a feature: bring that feature's entry into view once the list is in
  useEffect(() => {
    if (!open || !st || !surface) return
    dlg.current?.querySelector('.aihow__item--here')?.scrollIntoView?.({ block: 'nearest' })
  }, [open, st, surface])

  const served = (id) => {
    const s = st?.by_surface?.[id]
    if (!s) return null
    const n = (s.ok || 0) + (s.cached || 0)
    return n || s.fallback ? `${n} answered by Gemini this session${s.fallback ? `, ${s.fallback} on the plain version` : ''}` : null
  }

  return (
    <>
      <button type="button" className={`aihow__open${className ? ` ${className}` : ''}`} aria-haspopup="dialog" onClick={() => setOpen(true)}>
        {label}
      </button>
      <dialog
        ref={dlg}
        className="aihow"
        aria-labelledby="aihow-title"
        onClose={() => setOpen(false)}
        onClick={(e) => e.target === dlg.current && setOpen(false)}
        // keys stay inside the panel: a host's own shortcuts (the review stage's arrows and Escape) don't fire under it
        onKeyDown={(e) => e.stopPropagation()}
      >
        <div className="aihow__body">
          <div className="aihow__head">
            <h2 id="aihow-title">How AI is used</h2>
            <button type="button" className="aihow__x" onClick={() => setOpen(false)} aria-label="Close">
              Close
            </button>
          </div>
          {err && <ErrorBanner error={err} onRetry={() => setRetry((r) => r + 1)} />}
          {!st && !err && <Loading label="Loading…" />}
          {st && (
            <>
              <TrustCard st={st} />
              <h3 className="aihow__h3">Where Gemini is used</h3>
              <p className="aihow__rule">
                Gemini proposes or writes. The power-flow engine or the computed fact sheet checks it. Without a key, quota or network, a labeled plain version runs
                instead.
              </p>
              <ul className="aihow__list">
                {st.surfaces.map((s) => (
                  <li key={s.id} className={s.id === surface ? 'aihow__item--here' : undefined} aria-current={s.id === surface ? 'true' : undefined}>
                    <strong>
                      {s.name}
                      {s.id === surface && <span className="aihow__here">You opened this from here</span>}
                    </strong>
                    <span>
                      <em>Gemini:</em> {s.gemini}
                    </span>
                    <span>
                      <em>Checked by:</em> {s.check}
                    </span>
                    <span>
                      <em>Without it:</em> {s.fallback}
                    </span>
                    {served(s.id) && <span className="aihow__served">{served(s.id)}</span>}
                  </li>
                ))}
              </ul>
              <p className="aihow__none">
                <em>No AI at all:</em> the power flow, the cascade, the room at each substation, the site report and every cost formula. Those carry the
                &ldquo;Engine&rdquo; label.
              </p>
              <p className="aihow__foot">
                {st.configured ? `Model ${st.model}.` : 'Gemini is not configured on this server, so the plain versions are running.'} {st.used_today} of {st.cap} AI calls used today (shared by everyone).
                Everything is computed on a synthetic grid model (Breakthrough Energy / Texas A&amp;M), not a real utility&apos;s network.
              </p>
            </>
          )}
        </div>
      </dialog>
    </>
  )
}
