import { useCallback, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import AiBadge from '../ai/AiBadge'
import { fmtMonth, sourcesFor } from './agreementText'
import { fmtDate, fmtRange, kindText, statusText, toneOf } from './format'
import { localToday } from './plain'
import './agreement.css'

// Step 3 of a pair's sheet (PairSheet.jsx): the draft coordination proposal as a document. The plain (template)
// draft arrives at once; Gemini's wording replaces it when every number it wrote has passed the backend's check
// (GET /api/agreement/<id>). Print gives the document alone, black on white (the sheet portals a print copy).
// useAgreement.js fetches one version; Paper renders it; DraftStatus says who wrote it; WindowTimeline draws both
// projects' build windows on one axis (step 1 reuses it).

// the document alone for print: a copy portaled to <body> (agreement.css hides everything else while printing)
export function PrintCopy({ doc, t }) {
  return createPortal(
    <div className="gl gl-print">
      <Paper doc={doc} t={t} print />
    </div>,
    document.body,
  )
}

export function DraftStatus({ doc, pending, aiError, t, onRetry }) {
  return <Status doc={doc} pending={pending} aiError={aiError} t={t} onRetry={onRetry} />
}

function Status({ doc, pending, aiError, t, onRetry }) {
  if (!doc) return <span className="gl-fine">…</span>
  if (pending) {
    return (
      <span className="gl-doc__status">
        <AiBadge by="fallback" why="Gemini drafting" compact title={t.drafting} />
        <span className="gl-doc__pulse" aria-hidden="true" />
        <span className="gl-fine">{doc?.lang === 'es' ? 'Gemini redactando…' : 'Gemini drafting…'}</span>
      </span>
    )
  }
  if (doc.by === 'gemini') {
    return (
      <span className="gl-doc__status">
        <AiBadge by="gemini" lang={doc.lang} title="Worded by Gemini from the facts below; the backend checked every number it wrote against them">
          {doc.lang === 'es' ? 'cada número comprobado' : 'every number checked'}
        </AiBadge>
      </span>
    )
  }
  const why = aiError ? 'Gemini unavailable' : doc.fallback_reason === 'Plain version requested' ? 'plain version' : shortWhy(doc.fallback_reason)
  // a transient miss (offline, slow, an error) can be retried; a draft that failed the checks would fail again
  const retry = aiError || /unavailable|slow/i.test(doc.fallback_reason || '')
  return (
    <span className="gl-doc__status">
      <AiBadge by="fallback" why={why} className="aib--wrap" title={doc.fallback_reason || undefined} />
      {retry && (
        <button type="button" className="gl-link" onClick={onRetry}>
          {t.retry}
        </button>
      )}
    </span>
  )
}

function shortWhy(r) {
  if (!r) return 'Gemini unavailable'
  if (/failed/i.test(r)) return "Gemini's draft failed the checks"
  if (/slow/i.test(r)) return 'Gemini too slow'
  if (/not configured/i.test(r)) return 'Gemini not configured'
  return 'Gemini unavailable'
}

// The document itself (the screen panel and the print copy render the same thing).
export function Paper({ doc, t, print = false, onDropNeg }) {
  const d = doc.draft
  const byKey = Object.fromEntries((doc.facts || []).map((f) => [f.key, f]))
  const [factsOpen, setFactsOpen] = useState(false)
  const [flash, setFlash] = useState(null)
  const root = useRef(null)
  const jump = useCallback((k) => {
    setFactsOpen(true)
    setFlash(k)
    requestAnimationFrame(() => {
      // scroll the sheet's body, not the page
      const el = root.current?.querySelector(`[data-fact="${CSS.escape(k)}"]`)
      const body = el?.closest('.gl-sheet__body')
      if (!el || !body) return
      const top = body.scrollTop + el.getBoundingClientRect().top - body.getBoundingClientRect().top - body.clientHeight / 2
      body.scrollTo({ top: Math.max(0, top), behavior: 'smooth' })
    })
    setTimeout(() => setFlash((cur) => (cur === k ? null : cur)), 1600)
  }, [])
  const refs = (item) => <Refs item={item} byKey={byKey} onJump={jump} print={print} />
  const scope = d.sections.find((s) => s.id === 'scope')
  const why = d.sections.find((s) => s.id === 'why')
  const projects = doc.overlap?.projects || []

  return (
    <article className="gl-paper" ref={root} lang={doc.lang}>
      <p className="gl-paper__banner" role="note">
        {doc.disclaimer}
      </p>
      {d.negotiated && (
        <p className="gl-paper__neg" role="note">
          {doc.plan?.applied && doc.plan.text ? doc.plan.text.replace(/\.$/, '') : t.negotiated[d.negotiated.by] || d.negotiated.text}
          {d.negotiated.round ? ` (${t.negRound(d.negotiated.round)})` : ''}.
          {!print && onDropNeg && (
            <button type="button" className="gl-link" onClick={onDropNeg}>
              {t.dropNeg}
            </button>
          )}
        </p>
      )}
      <header className="gl-paper__mast">
        <span>{t.eyebrow}</span>
        <span>
          {t.generated} {new Date().toLocaleDateString(doc.lang === 'es' ? 'es' : 'en-US', { year: 'numeric', month: 'long', day: 'numeric' })}
        </span>
      </header>
      <h2 className="gl-paper__title">{d.title}</h2>

      <section className="gl-paper__parties" aria-label={t.parties}>
        {projects.map((p) => (
          <div key={p.id} className={`gl-party gl-party--${toneOf(p.utility)}`}>
            <span className="gl-party__who">
              <span className={`gl-swatch gl-swatch--${toneOf(p.utility)}`} aria-hidden="true" />
              {p.utility_name}
            </span>
            <strong className="gl-party__proj">{p.display_name}</strong>
            <span className="gl-party__meta">
              {[
                p.kv?.length ? `${p.kv.join(' / ')} kV` : null,
                kindText(p, t.lang),
                p.in_service ? `${t.inService} ${fmtDate(p.in_service, t.lang)}` : null,
                statusText(p.status, t.lang),
              ]
                .filter(Boolean)
                .join(' · ')}
            </span>
            <span className="gl-party__meta">
              {p.id} · {t.filed}{' '}
              {p.source?.url && !print ? (
                <a href={p.source.url} target="_blank" rel="noreferrer">
                  {p.source.label}
                </a>
              ) : (
                p.source?.label
              )}
            </span>
          </div>
        ))}
      </section>

      <p className="gl-paper__summary">
        {d.summary.text} {refs(d.summary)}
      </p>

      {scope && (
        <Sec n={1} title={t.scope}>
          <ul className="gl-paper__list">
            {scope.items.map((it, i) => (
              <li key={i}>
                {it.text} {refs(it)}
              </li>
            ))}
          </ul>
        </Sec>
      )}

      <Sec n={2} title={windowTitle(d.joint_window, t)}>
        <WindowTimeline jw={d.joint_window} parties={d.parties} t={t} />
        <p>
          {d.joint_window.text} {refs(d.joint_window)}
        </p>
      </Sec>

      <Sec n={3} title={t.roles}>
        <div className="gl-roles">
          {d.roles.map((r) => (
            <div key={r.side} className={`gl-role${r.side === 'both' ? ' gl-role--both' : ''}`}>
              <h4>
                {r.side !== 'both' && <span className={`gl-swatch gl-swatch--${toneOf(r.party)}`} aria-hidden="true" />}
                {r.side === 'both' ? t.both : r.name}
              </h4>
              <ul className="gl-paper__list">
                {r.does.map((it, i) => (
                  <li key={i}>
                    {it.text} {refs(it)}
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      </Sec>

      <Sec n={4} title={t.split}>
        <div className="gl-split" role="img" aria-label={d.cost_split.rule}>
          {d.cost_split.shares.map((s) => (
            <span key={s.side} className={`gl-split__part gl-split__part--${toneOf(s.party)}`} style={{ flexBasis: `${s.pct}%` }}>
              {s.short} {s.pct} %
            </span>
          ))}
        </div>
        <p className="gl-paper__rule">
          {d.cost_split.rule}
          {d.negotiated && <span className="gl-split__neg">{t.negTag}</span>}
        </p>
        <p>
          {d.cost_split.rationale} {refs(d.cost_split)}
        </p>
        <p className="gl-fine">{t.proposedRule}</p>
      </Sec>

      <Sec n={5} title={t.savings}>
        <p className="gl-save">
          <strong className="gl-save__range">{fmtRange(d.savings.low, d.savings.high, d.savings.unit)}</strong>
          <span className="gl-fine"> {t.roughEstimate}</span>
        </p>
        <ul className="gl-save__items">
          {(d.savings.items || []).map((it) => {
            const src = (d.savings.sources || []).find((s) => s.title === it.source)
            return (
              <li key={it.id}>
                <div className="gl-save__line">
                  <span>{it.label}</span>
                  <strong>{fmtRange(it.low, it.high, it.unit)}</strong>
                </div>
                <span className="gl-fine">
                  {it.basis}
                  {it.needs ? `. ${t.needs} ${it.needs}` : ''}.{' '}
                  {src?.url && !print ? (
                    <a className="gl-ref" href={src.url} target="_blank" rel="noreferrer">
                      {it.source}
                    </a>
                  ) : (
                    <span className="gl-ref">{it.source}</span>
                  )}
                </span>
              </li>
            )
          })}
        </ul>
        {d.savings.assumptions?.length > 0 && (
          <details className="gl-details" open={print || undefined}>
            <summary>
              {t.assumptions} ({d.savings.assumptions.length})
            </summary>
            <ul>
              {d.savings.assumptions.map((a) => (
                <li key={a}>{a}</li>
              ))}
            </ul>
          </details>
        )}
      </Sec>

      {why && (
        <Sec n={6} title={t.why}>
          <ul className="gl-paper__list">
            {why.items.map((it, i) => (
              <li key={i}>
                {it.text} {refs(it)}
              </li>
            ))}
          </ul>
        </Sec>
      )}

      <Sec n={7} title={t.steps}>
        <ol className="gl-paper__steps">
          {d.next_steps.map((it, i) => (
            <li key={i}>
              {it.text} {refs(it)}
            </li>
          ))}
        </ol>
      </Sec>

      <Sec n={8} title={t.conditions}>
        <ul className="gl-paper__list gl-paper__list--fine">
          {d.conditions.map((it, i) => (
            <li key={i}>{it.text}</li>
          ))}
        </ul>
      </Sec>

      <details
        className="gl-details gl-paper__facts"
        open={print || factsOpen || undefined}
        onToggle={(e) => !print && setFactsOpen(e.currentTarget.open)}
      >
        <summary>
          {t.facts} ({(doc.facts || []).length})
        </summary>
        <ol>
          {(doc.facts || []).map((f) => (
            <li key={f.key} data-fact={f.key} className={flash === f.key ? 'is-flash' : undefined}>
              {f.text}{' '}
              <span className="gl-ref gl-ref--plain">
                ({f.source?.url && !print ? (
                  <a href={f.source.url} target="_blank" rel="noreferrer">
                    {f.source.label}
                  </a>
                ) : (
                  f.source?.label
                )}
                )
              </span>
            </li>
          ))}
        </ol>
      </details>

      {!print && doc.rejected?.length > 0 && (
        <details className="gl-details gl-paper__rejected">
          <summary>{t.removed(doc.rejected.length)}</summary>
          <ul>
            {doc.rejected.map((r, i) => (
              <li key={i}>
                <span className="gl-mono">{r.where}</span>: &ldquo;{r.text}&rdquo; <span className="gl-fine">({r.reason})</span>
              </li>
            ))}
          </ul>
        </details>
      )}

      <footer className="gl-paper__foot">
        <p>{doc.by === 'gemini' ? t.by.gemini : t.by.template}</p>
        <p>{doc.generated_from}</p>
      </footer>
    </article>
  )
}

function Sec({ n, title, children }) {
  return (
    <section className="gl-paper__sec">
      <h3>
        <span className="gl-paper__n">{n}.</span> {title}
      </h3>
      {children}
    </section>
  )
}

// small source links after a sentence: the filing page, the comparison, the estimate's source
function Refs({ item, byKey, onJump, print }) {
  const list = sourcesFor(item?.facts, byKey)
  if (!list.length) return null
  return (
    <span className="gl-refs">
      {list.map((s, i) => {
        const tip = s.facts.map((f) => f.text).join('\n')
        return (
          <span key={s.label}>
            {i > 0 && <span aria-hidden="true"> · </span>}
            {print ? (
              <span className="gl-ref">{s.label}</span>
            ) : s.url ? (
              <a className="gl-ref" href={s.url} target="_blank" rel="noreferrer" title={tip}>
                {s.label}
              </a>
            ) : (
              <button type="button" className="gl-ref" title={tip} onClick={() => onJump(s.key)}>
                {s.label}
              </button>
            )}
          </span>
        )
      })}
    </span>
  )
}

// section 2's heading: a window that ended before the draft's date is not proposed, only reported as filed
function windowTitle(jw, t) {
  return jw.status === 'past' || (!jw.overlap && jw.past_sides?.length) ? t.windowPast : t.window
}

// both projects' build windows on one time axis, the months they share, and the draft's date
export function WindowTimeline({ jw, parties, t }) {
  const rows = [
    ['a', jw.a],
    ['b', jw.b],
  ].filter(([, w]) => w)
  if (!rows.length) return null
  const ms = (iso) => new Date(`${iso}T00:00:00Z`).getTime()
  const y0 = Math.min(...rows.map(([, w]) => new Date(`${w.start}T00:00:00Z`).getUTCFullYear()))
  const y1 = Math.max(...rows.map(([, w]) => new Date(`${w.end}T00:00:00Z`).getUTCFullYear())) + 1
  const lo = Date.UTC(y0, 0, 1)
  const hi = Date.UTC(y1, 0, 1)
  const pos = (iso) => ((ms(iso) - lo) / (hi - lo)) * 100
  const years = []
  for (let y = y0; y <= y1; y++) years.push(y)
  const step = years.length > 8 ? 2 : 1
  // today in the viewer's time zone (the draft's "Generated" date uses the same one; the server's date is UTC)
  const today = localToday()
  const now = ms(today) >= lo && ms(today) <= hi ? pos(today) : null
  const at = (p) => `calc(var(--tl-who) + (100% - var(--tl-who)) * ${p / 100})`
  // short labels: the dates are on each project's bar and in the sentence below
  const jointLabel =
    jw.status === 'past'
      ? t.jointPast
      : jw.status === 'open'
        ? `${t.jointOpen} ${fmtMonth(jw.end, t.lang)}`
        : `${t.joint}: ${fmtMonth(jw.start, t.lang)} – ${fmtMonth(jw.end, t.lang)}`
  return (
    <div className={`gl-tl${now != null ? ' has-now' : ''}`} role="img" aria-label={jw.text}>
      <div className="gl-tl__grid">
        {rows.map(([side, w]) => {
          const p = parties.find((x) => x.side === side)
          return (
            <div key={side} className="gl-tl__row">
              <span className="gl-tl__who">{p?.short}</span>
              <span className="gl-tl__track">
                <span
                  className={`gl-tl__bar gl-tl__bar--${toneOf(p?.code)}${w.assumed ? ' gl-tl__bar--derived' : ''}`}
                  style={{ left: `${pos(w.start)}%`, width: `${Math.max(0.8, pos(w.end) - pos(w.start))}%` }}
                  title={`${fmtMonth(w.start, t.lang)} – ${fmtMonth(w.end, t.lang)}: ${w.basis}`}
                />
                <span className="gl-tl__dates" style={{ left: `${Math.min(pos(w.start), 70)}%` }}>
                  {fmtMonth(w.start, t.lang)} – {fmtMonth(w.end, t.lang)}
                  {w.assumed ? ` (${t.derived})` : ''}
                </span>
              </span>
            </div>
          )
        })}
        {jw.overlap && jw.start && (
          <span
            className={`gl-tl__joint${pos(jw.start) > 55 ? ' is-right' : ''}${jw.status === 'past' ? ' is-past' : ''}`}
            style={{ left: at(pos(jw.start)), width: `calc((100% - var(--tl-who)) * ${(pos(jw.end) - pos(jw.start)) / 100})` }}
          >
            {!jw.negotiated && (
              <span className="gl-tl__joint-label" title={`${fmtMonth(jw.start, t.lang)} – ${fmtMonth(jw.end, t.lang)}`}>
                {jointLabel}
              </span>
            )}
          </span>
        )}
        {jw.negotiated && (
          <span
            className={`gl-tl__neg${pos(jw.negotiated.start) > 55 ? ' is-right' : ''}`}
            style={{ left: at(pos(jw.negotiated.start)), width: `calc((100% - var(--tl-who)) * ${Math.max(0.8, pos(jw.negotiated.end) - pos(jw.negotiated.start)) / 100})` }}
            title={`${fmtMonth(jw.negotiated.start, t.lang)} – ${fmtMonth(jw.negotiated.end, t.lang)}`}
          >
            <span className="gl-tl__neg-label">
              {t.negTag}: {fmtMonth(jw.negotiated.start, t.lang)} – {fmtMonth(jw.negotiated.end, t.lang)}
            </span>
          </span>
        )}
        {now != null && <span className="gl-tl__now" style={{ left: at(now) }} />}
      </div>
      <div className="gl-tl__axis" aria-hidden="true">
        {years
          .filter((y, i) => i % step === 0)
          .map((y) => (
            <span key={y} className={y === y1 ? 'is-last' : undefined} style={{ left: at(((Date.UTC(y, 0, 1) - lo) / (hi - lo)) * 100) }}>
              {y}
            </span>
          ))}
        {now != null && (
          <span className={`gl-tl__now-label${now > 80 ? ' is-right' : now < 12 ? ' is-left' : ''}`} style={{ left: at(now) }}>
            {t.today} {fmtDate(today, t.lang)}
          </span>
        )}
      </div>
      {!jw.overlap && <p className="gl-fine">{t.noJoint}</p>}
    </div>
  )
}
