import { useCallback, useEffect, useRef, useState } from 'react'
import AiBadge from '../ai/AiBadge'
import { Button, ErrorBanner } from '../../ui'
import { fmtMonth } from './agreementText'
import { fmtRange, toneOf } from './format'
import './negotiation.css'

// Negotiate: two Gemini agents, each reading ONE utility's public filing, trade structured proposals for the drafted
// agreement's terms (joint window, shared items, split rule). After every turn the pipeline (deterministic code, not
// Gemini) checks it against both filings; a rejected turn goes back to its agent with the findings. Two columns, A in
// DESC blue on the left and B in Georgia gold on the right, one row per round. Not a chat: nobody types here. The
// turns come from POST /api/negotiate/<id>; while it runs the page polls /live, so each turn and its verdict appear
// as they happen (a cached negotiation replays one turn at a time; reduced motion shows everything at once).

const STEP_MS = 700
const POLL_MS = 700

const T = {
  en: {
    title: 'Two AI agents negotiate the terms',
    start: 'Let two AI agents negotiate it',
    pitch: (a, b) =>
      `One agent reads only ${a}'s public filing, the other only ${b}'s. They trade proposals for the joint window, what to share and the cost split; the pipeline checks every turn against both filings.`,
    notUtilities: 'The agents are not the utilities and cannot speak for them.',
    agent: 'Agent',
    reads: (s) => `reads ${s}'s filing`,
    asFiled: 'Window as filed',
    ended: 'ended',
    round: 'Round',
    kind: { propose: 'Opens', counter: 'Counters', revise: 'Revises', accept: 'Accepts the proposal on the table' },
    changed: 'changed',
    verified: 'Verified against the filings',
    rejected: 'Rejected',
    sentBack: 'Sent back to the agent with these findings',
    concerns: 'From its filing',
    working: (a, s, rev) => (rev ? `Agent ${a} is revising after the pipeline's findings…` : `Agent ${a} is reading ${s}'s filing and answering…`),
    starting: 'Starting the negotiation…',
    calls: (n, max) => `Gemini call ${n} of up to ${max}`,
    agreed: (r) => `Agreed in round ${r}`,
    noDeal: 'No agreement',
    savings: 'Estimated savings for the agreed items',
    use: 'Use these terms in the draft',
    inDraft: 'These terms are in the draft',
    drop: "Use the draft's own terms",
    hide: 'Hide the exchange',
    show: 'Show the exchange',
    by: (m, calls, s) => `${m} · ${calls} call${calls === 1 ? '' : 's'} · ${s}`,
    cached: 'replayed from an earlier run',
    plainWhy: 'Plain version: A opens, B counters, they settle on the filed-length split.',
    retried: 'fast model',
  },
  es: {
    title: 'Dos agentes de IA negocian los términos',
    start: 'Que lo negocien dos agentes de IA',
    pitch: (a, b) =>
      `Un agente lee solo el documento público de ${a}, el otro solo el de ${b}. Intercambian propuestas de ventana conjunta, qué compartir y el reparto de costos; el sistema comprueba cada turno con los dos documentos.`,
    notUtilities: 'Los agentes no son las empresas y no pueden hablar por ellas.',
    agent: 'Agente',
    reads: (s) => `lee el documento de ${s}`,
    asFiled: 'Ventana publicada',
    ended: 'terminó',
    round: 'Ronda',
    kind: { propose: 'Abre', counter: 'Contrapropone', revise: 'Corrige', accept: 'Acepta la propuesta sobre la mesa' },
    changed: 'cambia',
    verified: 'Verificado con los documentos',
    rejected: 'Rechazado',
    sentBack: 'Devuelto al agente con estos hallazgos',
    concerns: 'De su documento',
    working: (a, s, rev) => (rev ? `El agente ${a} corrige tras los hallazgos del sistema…` : `El agente ${a} lee el documento de ${s} y responde…`),
    starting: 'Empezando la negociación…',
    calls: (n, max) => `Llamada a Gemini ${n} de hasta ${max}`,
    agreed: (r) => `Acuerdo en la ronda ${r}`,
    noDeal: 'Sin acuerdo',
    savings: 'Ahorro estimado de las partidas acordadas',
    use: 'Usar estos términos en el borrador',
    inDraft: 'Estos términos están en el borrador',
    drop: 'Usar los términos propios del borrador',
    hide: 'Ocultar el intercambio',
    show: 'Mostrar el intercambio',
    by: (m, calls, s) => `${m} · ${calls} llamada${calls === 1 ? '' : 's'} · ${s}`,
    cached: 'repetido de una ejecución anterior',
    plainWhy: 'Versión simple: A abre, B contrapropone y acuerdan el reparto por longitud publicada.',
    retried: 'modelo rápido',
  },
}

function useReduced() {
  const [r] = useState(() => {
    try {
      return window.matchMedia('(prefers-reduced-motion: reduce)').matches
    } catch {
      return false
    }
  })
  return r
}

const secs = (ms) => (ms == null ? '' : ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(1)} s`)

// which of a turn's terms differ from the verified proposal before it (the one on the table)
function changedKeys(turns, i) {
  const t = turns[i]
  if (t.kind === 'accept') return new Set()
  for (let j = i - 1; j >= 0; j--) {
    const p = turns[j]
    if (!p.verdict.ok) continue
    const prev = Object.fromEntries(p.plain.map((d) => [d.k, d.text]))
    return new Set(t.plain.filter((d) => prev[d.k] !== d.text).map((d) => d.k))
  }
  return new Set()
}

export default function Negotiation({ client, draftId, months, lang = 'en', parties, used, onUse }) {
  const t = T[lang] || T.en
  const reduced = useReduced()
  const [run, setRun] = useState({ phase: 'idle' })
  const [live, setLive] = useState(null)
  const [shown, setShown] = useState(0)
  const [open, setOpen] = useState(true)
  const token = useRef(null)
  const poll = useRef(null)

  useEffect(
    () => () => {
      token.current = null
      clearInterval(poll.current)
    },
    [],
  )

  const start = useCallback(() => {
    const me = {}
    token.current = me
    clearInterval(poll.current)
    setRun({ phase: 'running' })
    setLive(null)
    setShown(0)
    setOpen(true)
    poll.current = setInterval(() => {
      client.negotiateLive(draftId, { window_months: months, lang }).then(
        (d) => token.current === me && d?.running && setLive(d),
        () => {},
      )
    }, POLL_MS)
    client.negotiate(draftId, { window_months: months, lang, ai: true }).then(
      (data) => {
        if (token.current !== me) return
        clearInterval(poll.current)
        setRun({ phase: 'done', data })
      },
      (error) => {
        if (token.current !== me) return
        clearInterval(poll.current)
        setRun({ phase: 'error', error })
      },
    )
  }, [client, draftId, months, lang])

  const data = run.phase === 'done' ? run.data : null
  const turns = data ? data.turns : live?.turns || []
  // one turn at a time: live turns appear as they happen, a finished (or cached) run replays in order
  useEffect(() => {
    if (reduced || shown >= turns.length) return undefined
    const id = setTimeout(() => setShown((s) => Math.min(turns.length, s + 1)), shown === 0 ? 180 : STEP_MS)
    return () => clearTimeout(id)
  }, [reduced, shown, turns.length])
  const n = reduced ? turns.length : Math.min(shown, turns.length)
  const finished = !!data && n >= turns.length

  const agents = data?.agents || null
  const A = parties?.find((p) => p.side === 'a')
  const B = parties?.find((p) => p.side === 'b')
  const shortOf = (side) => (agents ? agents.find((x) => x.side === side)?.short : side === 'a' ? A?.short : B?.short) || side.toUpperCase()
  const toneFor = (side) => toneOf((agents ? agents.find((x) => x.side === side)?.utility : side === 'a' ? A?.code : B?.code) || '')

  if (run.phase === 'idle') {
    return (
      <section className="gl-neg gl-neg--idle" aria-label={t.title}>
        <p className="gl-neg__pitch">
          {t.pitch(A?.short || 'A', B?.short || 'B')} <span className="gl-fine">{t.notUtilities}</span>
        </p>
        <Button onClick={start}>{t.start}</Button>
      </section>
    )
  }

  const rounds = []
  turns.slice(0, n).forEach((turn, i) => {
    let r = rounds.find((x) => x.round === turn.round)
    if (!r) rounds.push((r = { round: turn.round, a: [], b: [] }))
    // the pipeline's notes only where they change (the same caveat on every card is noise)
    const notes = (turn.verdict.notes || []).join(' ')
    const repeat = i > 0 && notes === (turns[i - 1].verdict.notes || []).join(' ')
    r[turn.agent].push({ turn, changed: changedKeys(turns, i), notes: repeat ? '' : notes })
  })
  const outcome = data?.outcome
  const useKey = data ? (data.by === 'gemini' ? data.lang : 'plain') : null
  const inDraft = useKey != null && used === useKey

  return (
    <section className={`gl-neg${finished && outcome?.agreed ? ' is-agreed' : ''}`} aria-label={t.title} aria-busy={run.phase === 'running' || undefined}>
      <header className="gl-neg__head">
        <h3>{t.title}</h3>
        {data?.by === 'gemini' && (
          <AiBadge by="gemini" lang={lang} className="aib--wrap" title="Two Gemini agents proposed; the pipeline checked every turn against both filings">
            {lang === 'es' ? 'cada turno comprobado con los documentos' : 'every turn checked against the filings'}
          </AiBadge>
        )}
        {data && data.by !== 'gemini' && <AiBadge by="fallback" lang={lang} why={data.fallback_reason} className="aib--wrap" />}
        {run.phase === 'running' && <AiBadge by="gemini" lang={lang}>{lang === 'es' ? 'negociando' : 'negotiating'}</AiBadge>}
        <button type="button" className="gl-link gl-neg__toggle" onClick={() => setOpen((o) => !o)} aria-expanded={open}>
          {open ? t.hide : t.show}
        </button>
      </header>

      {open && (
        <>
          <p className="gl-fine gl-neg__how">
            {data?.how || t.pitch(A?.short || 'A', B?.short || 'B')} {t.notUtilities}
          </p>

          {agents && (
            <div className="gl-neg__agents">
              {agents.map((a) => (
                <div key={a.side} className={`gl-neg__agent gl-neg__agent--${toneOf(a.utility)}`}>
                  <span className="gl-neg__who">
                    <span className={`gl-swatch gl-swatch--${toneOf(a.utility)}`} aria-hidden="true" />
                    {t.agent} {a.side.toUpperCase()} · {t.reads(a.short)}
                  </span>
                  <span className="gl-neg__rep">{a.represents}</span>
                  <span className="gl-fine">
                    {a.project_id} · {[a.kv?.length ? `${a.kv.join(' / ')} kV` : null, a.kind_label, a.miles ? `${a.miles} mi` : null].filter(Boolean).join(' · ')}
                  </span>
                  {a.window && (
                    <span className="gl-fine">
                      {t.asFiled}: {fmtMonth(a.window.start)} – {fmtMonth(a.window.end)}
                      {a.window.ended ? ` (${t.ended})` : ''}
                    </span>
                  )}
                  <span className="gl-fine">
                    {a.source?.url ? (
                      <a className="gl-ref" href={a.source.url} target="_blank" rel="noreferrer">
                        {a.filing}
                      </a>
                    ) : (
                      a.filing
                    )}
                    {' · '}
                    {a.not_the_utility}
                  </span>
                </div>
              ))}
            </div>
          )}

          <ol className="gl-neg__rounds">
            {rounds.map((r) => (
              <li key={r.round} className="gl-neg__round">
                <span className="gl-neg__rlabel">
                  {t.round} {r.round}
                </span>
                <div className="gl-neg__cols">
                  {['a', 'b'].map((side) => (
                    <div key={side} className="gl-neg__col">
                      {r[side].map(({ turn, changed, notes }) => (
                        <Turn key={turn.n} turn={turn} changed={changed} notes={notes} t={t} tone={toneFor(side)} who={shortOf(side)} />
                      ))}
                    </div>
                  ))}
                </div>
              </li>
            ))}
          </ol>

          {run.phase === 'running' && (
            <p className="gl-neg__working" role="status">
              <span className="gl-doc__pulse" aria-hidden="true" />
              {live?.working ? t.working(live.working.agent.toUpperCase(), shortOf(live.working.agent), live.working.revision) : t.starting}
              {live ? <span className="gl-fine"> · {t.calls(Math.min(live.calls + (live.working ? 1 : 0), live.max_calls), live.max_calls)} · {secs(live.elapsed_ms)}</span> : null}
            </p>
          )}
          {run.phase === 'error' && <ErrorBanner error={run.error} onRetry={start} />}
        </>
      )}

      {finished && outcome && (
        <Outcome outcome={outcome} data={data} t={t} inDraft={inDraft} onUse={() => onUse?.(useKey)} onDrop={() => onUse?.(null)} />
      )}
    </section>
  )
}

function Turn({ turn, changed, notes, t, tone, who }) {
  const v = turn.verdict
  const p = turn.proposal
  const accept = turn.kind === 'accept'
  return (
    <article className={`gl-turn gl-turn--${tone}${v.ok ? ' is-ok' : ' is-bad'}${accept ? ' is-accept' : ''}`}>
      <div className="gl-turn__top">
        <span className="gl-turn__kind">
          <span className="gl-turn__who">{who}</span> {t.kind[turn.kind] || turn.kind}
        </span>
        <span className="gl-turn__ms">
          {turn.cached ? '' : secs(turn.ms)}
          {turn.retried ? ` · ${t.retried}` : ''}
        </span>
      </div>
      {!accept && (
        <dl className="gl-turn__terms">
          {turn.plain.map((d) => (
            <div key={d.k} className={changed.has(d.k) ? 'is-changed' : undefined}>
              <dt>{d.label}</dt>
              <dd>
                {d.text}
                {changed.has(d.k) && <span className="gl-turn__chg"> {t.changed}</span>}
              </dd>
            </div>
          ))}
        </dl>
      )}
      {p.concerns?.length > 0 && (
        <ul className="gl-turn__concerns" aria-label={t.concerns}>
          {p.concerns.map((c, i) => (
            <li key={i}>{c}</li>
          ))}
        </ul>
      )}
      {p.note && <p className="gl-turn__note">{p.note}</p>}
      {v.ok ? (
        <p className="gl-turn__verdict is-ok">{t.verified}</p>
      ) : (
        <div className="gl-turn__verdict is-bad">
          <strong>{t.rejected}:</strong> {v.findings[0]}
          {v.findings.length > 1 && (
            <ul>
              {v.findings.slice(1).map((f, i) => (
                <li key={i}>{f}</li>
              ))}
            </ul>
          )}
          <span className="gl-fine gl-turn__back">{t.sentBack}</span>
        </div>
      )}
      {notes && <p className="gl-fine gl-turn__notes">{notes}</p>}
    </article>
  )
}

function Outcome({ outcome, data, t, inDraft, onUse, onDrop }) {
  const o = outcome
  const meta =
    data.by === 'gemini' ? t.by(data.models?.join(' + ') || data.model || 'Gemini', data.calls, data.cached ? t.cached : secs(data.ms)) : t.plainWhy
  if (!o.agreed) {
    return (
      <div className="gl-neg__outcome is-none" role="status">
        <h4>{t.noDeal}</h4>
        <p>{o.reason}</p>
        <p className="gl-fine">{meta}</p>
      </div>
    )
  }
  const terms = o.terms
  return (
    <div className="gl-neg__outcome is-agreed" role="status">
      <h4>
        {t.agreed(o.round)} <span className="gl-neg__ok">{t.verified}</span>
      </h4>
      <dl className="gl-turn__terms gl-neg__final">
        {o.plain.map((d) => (
          <div key={d.k}>
            <dt>{d.label}</dt>
            <dd>{d.text}</dd>
          </div>
        ))}
      </dl>
      <p className="gl-neg__save">
        <span className="gl-fine">{t.savings}</span> <strong>{fmtRange(terms.savings.low, terms.savings.high, terms.savings.unit)}</strong>
      </p>
      {o.next && <p className="gl-neg__next">{o.next}</p>}
      {o.final_check?.notes?.length > 0 && <p className="gl-fine">{o.final_check.notes.join(' ')}</p>}
      <p className="gl-fine">{o.reason}</p>
      <div className="gl-neg__actions">
        {inDraft ? (
          <>
            <span className="gl-neg__indraft">{t.inDraft}</span>
            <button type="button" className="gl-link" onClick={onDrop}>
              {t.drop}
            </button>
          </>
        ) : (
          <Button onClick={onUse} disabled={!o.verified}>
            {t.use}
          </Button>
        )}
        <span className="gl-fine">{meta}</span>
      </div>
    </div>
  )
}
