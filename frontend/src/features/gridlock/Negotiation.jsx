import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import AiBadge from '../ai/AiBadge'
import HearNegotiation from './HearNegotiation'
import { negotiationLines } from './hearLines'
import { Button, ErrorBanner } from '../../ui'
import { fmtMonth } from './agreementText'
import { fmtRange, toneOf } from './format'
import './negotiation.css'

// Step 2 of a pair's sheet: two Gemini agents, each reading ONE utility's public filing, trade structured proposals
// for the draft's terms (joint window, what to share, the split rule). After every turn the pipeline (deterministic
// code, not Gemini) checks it against both filings; a rejected turn goes back to its agent with the findings. The
// exchange reads as one timeline: each turn is a compact row of terms with the check's verdict, the agent's own
// words behind a per-turn fold; then the result. Nobody types here. The turns come from POST /api/negotiate/<id>;
// while it runs the page polls /live so each turn appears as it happens (a cached run replays one at a time;
// reduced motion shows everything at once). onStatus reports {phase, agreed, round} to the sheet's stepper.
// The sheet keys this by the pair and window only: switching EN/ES keeps a run (its labels follow the language; the
// agents' own words stay in the language they negotiated in, and a line says so); a new run uses the current one.

const STEP_MS = 650
const POLL_MS = 700

const T = {
  en: {
    start: 'Let two AI agents negotiate it',
    pitch:
      "Each agent reads only one utility's filing. They trade proposals for a joint window, what to share and the cost split; after every turn the pipeline checks the proposal against both filings and sends a rejected one back.",
    checked: 'every turn checked against the filings',
    notUtilities: 'The agents are not the utilities and cannot speak for them.',
    agent: (s) => `${s}'s agent`,
    reads: (s) => `Reads only ${s}'s filing`,
    asFiled: 'window as filed',
    ended: 'ended',
    round: (r) => `Round ${r}`,
    kind: { propose: 'opens', counter: 'counters', revise: 'revises', accept: 'accepts the proposal on the table' },
    changed: 'changed',
    verified: 'Verified against the filings',
    rejected: 'Rejected',
    sentBack: 'Sent back to the agent with this finding.',
    more: 'What the agent said',
    concerns: 'From its filing',
    findings: 'Every finding',
    notes: "The pipeline's notes",
    working: (s, rev) => (rev ? `${s}'s agent is revising after the pipeline's finding` : `${s}'s agent is reading its filing and answering`),
    starting: 'Starting the negotiation',
    calls: (n, max) => `Gemini call ${n} of up to ${max}`,
    agreed: (r) => `Agreed in round ${r}`,
    agreedSub: 'The pipeline re-checked the final terms against both filings.',
    noDeal: 'No agreement',
    savings: 'Estimated savings for the agreed items',
    use: 'Use these terms in the draft',
    inDraft: 'These terms are in the draft below',
    drop: "Use the draft's own terms",
    by: (m, calls, s) => `${m}, ${calls} call${calls === 1 ? '' : 's'}, ${s}`,
    cached: 'replayed from an earlier run',
    plainWhy: 'Plain version: A opens, B counters, they settle on the filed-length split.',
    retried: 'fast model',
    how: 'How the check works',
    nothingH: 'Nothing to negotiate yet',
    nothing: (what) =>
      `At this distance, what the two projects could share (${what}) needs their build windows to share months, and they don't. The agents would have no terms to trade.`,
    nothingNext: 'A shared window would make one mobilization possible: comparing detailed schedules comes first.',
    terms: { window: 'Window', scope: 'Share', split: 'Split' },
    ranIn: { en: 'Negotiated in English; the agents\' words are shown as written.', es: 'Negotiated in Spanish; the agents\' words are shown as written.' },
  },
  es: {
    start: 'Que lo negocien dos agentes de IA',
    pitch:
      'Cada agente lee solo el documento de una empresa. Intercambian propuestas de ventana conjunta, qué compartir y el reparto de costos; después de cada turno el sistema comprueba la propuesta con los dos documentos y devuelve la rechazada.',
    checked: 'cada turno comprobado con los documentos',
    notUtilities: 'Los agentes no son las empresas y no pueden hablar por ellas.',
    agent: (s) => `Agente de ${s}`,
    reads: (s) => `Lee solo el documento de ${s}`,
    asFiled: 'ventana publicada',
    ended: 'terminó',
    round: (r) => `Ronda ${r}`,
    kind: { propose: 'abre', counter: 'contrapropone', revise: 'corrige', accept: 'acepta la propuesta sobre la mesa' },
    changed: 'cambia',
    verified: 'Verificado con los documentos',
    rejected: 'Rechazado',
    sentBack: 'Devuelto al agente con este hallazgo.',
    more: 'Lo que dijo el agente',
    concerns: 'De su documento',
    findings: 'Todos los hallazgos',
    notes: 'Notas del sistema',
    working: (s, rev) => (rev ? `El agente de ${s} corrige tras el hallazgo del sistema` : `El agente de ${s} lee su documento y responde`),
    starting: 'Empezando la negociación',
    calls: (n, max) => `Llamada a Gemini ${n} de hasta ${max}`,
    agreed: (r) => `Acuerdo en la ronda ${r}`,
    agreedSub: 'El sistema volvió a comprobar los términos finales con los dos documentos.',
    noDeal: 'Sin acuerdo',
    savings: 'Ahorro estimado de las partidas acordadas',
    use: 'Usar estos términos en el borrador',
    inDraft: 'Estos términos están en el borrador de abajo',
    drop: 'Usar los términos propios del borrador',
    by: (m, calls, s) => `${m}, ${calls} llamada${calls === 1 ? '' : 's'}, ${s}`,
    cached: 'repetido de una ejecución anterior',
    plainWhy: 'Versión simple: A abre, B contrapropone y acuerdan el reparto por longitud publicada.',
    retried: 'modelo rápido',
    how: 'Cómo funciona la comprobación',
    nothingH: 'Aún no hay nada que negociar',
    nothing: (what) =>
      `A esta distancia, lo que los dos proyectos podrían compartir (${what}) necesita que sus ventanas de obra compartan meses, y no los comparten. Los agentes no tendrían términos que intercambiar.`,
    nothingNext: 'Una ventana compartida permitiría una sola movilización: primero hay que comparar los calendarios detallados.',
    terms: { window: 'Ventana', scope: 'Compartir', split: 'Reparto' },
    ranIn: { en: 'Negociado en inglés; se muestra lo que escribieron los agentes.', es: 'Negociado en español; se muestra lo que escribieron los agentes.' },
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

export default function Negotiation({ client, draftId, months, lang = 'en', parties, names, used, onUse, onAgreed, onStatus, nothing }) {
  const t = T[lang] || T.en
  const reduced = useReduced()
  const [run, setRun] = useState({ phase: 'idle' })
  const [live, setLive] = useState(null)
  const [shown, setShown] = useState(0)
  const [speaking, setSpeaking] = useState(null) // the turn (or 'summary') being read aloud
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
    setRun({ phase: 'running', lang })
    setLive(null)
    setShown(0)
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
        setRun({ phase: 'done', data, lang })
      },
      (error) => {
        if (token.current !== me) return
        clearInterval(poll.current)
        setRun({ phase: 'error', error, lang })
      },
    )
  }, [client, draftId, months, lang])

  const data = run.phase === 'done' ? run.data : null
  const turns = data ? data.turns : live?.turns || []
  // one turn at a time: live turns appear as they happen, a finished (or cached) run replays in order
  useEffect(() => {
    if (reduced || shown >= turns.length) return undefined
    const id = setTimeout(() => setShown((s) => Math.min(turns.length, s + 1)), shown === 0 ? 160 : STEP_MS)
    return () => clearTimeout(id)
  }, [reduced, shown, turns.length])
  const n = reduced ? turns.length : Math.min(shown, turns.length)
  const finished = !!data && n >= turns.length
  const hearLines = useMemo(() => negotiationLines(data), [data])
  const outcome = data?.outcome

  // the sheet's stepper shows where the negotiation stands (and which terms "Use these terms" would apply)
  const phase = run.phase === 'done' && !finished ? 'running' : run.phase
  const agreedKey = data && outcome?.agreed && outcome?.verified ? (data.by === 'gemini' ? data.lang : 'plain') : null
  useEffect(() => {
    onStatus?.({ phase, agreed: !!outcome?.agreed, round: outcome?.round ?? null, verified: !!outcome?.verified, key: agreedKey })
  }, [onStatus, phase, outcome?.agreed, outcome?.round, outcome?.verified, agreedKey])
  // agreed and verified: the draft takes these terms by itself once the replay has shown them (the sheet skips it when
  // the viewer chose the draft's own terms for this pair)
  useEffect(() => {
    if (finished && agreedKey) onAgreed?.(agreedKey)
  }, [finished, agreedKey, onAgreed])

  // who each agent reads for: the run's agents, else the draft's parties, else the utilities the sheet knows (so a
  // draft that failed to load never leaves "A's agent")
  const agents = data?.agents || null
  const party = (side) => parties?.find((p) => p.side === side)
  const shortOf = (side) => agents?.find((x) => x.side === side)?.short || party(side)?.short || names?.[side]?.short || side.toUpperCase()
  const toneFor = (side) => toneOf(agents?.find((x) => x.side === side)?.utility || party(side)?.code || names?.[side]?.code || '')

  const agentsRow = (
    <div className="gl-neg__agents">
      {['a', 'b'].map((side) => {
        const a = agents?.find((x) => x.side === side)
        return (
          <div key={side} className={`gl-agent gl-agent--${toneFor(side)}`}>
            <span className="gl-agent__who">
              <span className={`gl-swatch gl-swatch--${toneFor(side)}`} aria-hidden="true" />
              {t.agent(shortOf(side))}
            </span>
            <span className="gl-agent__reads">
              {t.reads(shortOf(side))}
              {a?.source?.url && (
                <>
                  {': '}
                  <a href={a.source.url} target="_blank" rel="noreferrer">
                    {a.filing}
                  </a>
                </>
              )}
            </span>
            {a?.window && (
              <span className="gl-agent__win">
                {fmtMonth(a.window.start, lang)} – {fmtMonth(a.window.end, lang)}, {t.asFiled}
                {a.window.ended ? ` (${t.ended})` : ''}
              </span>
            )}
          </div>
        )
      })}
    </div>
  )

  // nothing in the estimate applies to this pair (no shared build window at a distance where only crews could be
  // shared): no agent is asked; the reason, in words
  if (nothing && run.phase === 'idle') {
    const what = (nothing.left_out || []).map((x) => x.label.toLowerCase()).join(', ') || (lang === 'es' ? 'cuadrillas y equipos' : 'crews and equipment')
    return (
      <div className="gl-neg gl-neg--nothing" role="status">
        <h4>{t.nothingH}</h4>
        <p>{t.nothing(what)}</p>
        <p className="gl-fine">{t.nothingNext}</p>
      </div>
    )
  }

  if (run.phase === 'idle') {
    return (
      <div className="gl-neg gl-neg--idle">
        {agentsRow}
        <p className="gl-neg__pitch">{t.pitch}</p>
        <div className="gl-neg__go">
          <Button onClick={start}>{t.start}</Button>
          <AiBadge by="gemini" lang={lang} className="aib--wrap">
            {t.checked}
          </AiBadge>
        </div>
        <p className="gl-fine">{t.notUtilities}</p>
      </div>
    )
  }

  const useKey = data ? (data.by === 'gemini' ? data.lang : 'plain') : null
  const inDraft = useKey != null && used === useKey
  // the pipeline's notes only where they change (the same caveat on every turn is noise)
  const freshNotes = []
  for (let i = 0; i < turns.length; i++) {
    const notes = (turns[i].verdict.notes || []).join(' ')
    const prev = i > 0 ? (turns[i - 1].verdict.notes || []).join(' ') : ''
    freshNotes.push(notes && notes !== prev ? notes : '')
  }

  return (
    <div className={`gl-neg${finished && outcome?.agreed ? ' is-agreed' : ''}`} aria-busy={phase === 'running' || undefined}>
      <div className="gl-neg__labels">
        {data?.by === 'gemini' && (
          <AiBadge by="gemini" lang={lang} className="aib--wrap" title="Two Gemini agents proposed; the pipeline checked every turn against both filings">
            {t.checked}
          </AiBadge>
        )}
        {data && data.by !== 'gemini' && <AiBadge by="fallback" lang={lang} why={data.fallback_reason} className="aib--wrap" />}
        {!data && <AiBadge by="gemini" lang={lang}>{lang === 'es' ? 'negociando' : 'negotiating'}</AiBadge>}
        <span className="gl-fine">{t.notUtilities}</span>
        {run.lang && run.lang !== lang && <span className="gl-fine gl-neg__ranin">{t.ranIn[run.lang]}</span>}
      </div>
      {agentsRow}

      {finished && data?.voice && <HearNegotiation lines={hearLines} lang={lang} onActive={setSpeaking} />}

      <ol className="gl-tl2" aria-live="polite">
        {turns.slice(0, n).map((turn, i) => {
          const prevRound = i > 0 ? turns[i - 1].round : null
          return (
            <Turn
              key={turn.n}
              turn={turn}
              newRound={turn.round !== prevRound}
              changed={changedKeys(turns, i)}
              notes={freshNotes[i]}
              t={t}
              tone={toneFor(turn.agent)}
              who={shortOf(turn.agent)}
              speaking={speaking === turn.n}
            />
          )
        })}
        {phase === 'running' && (
          <li className={`gl-tn gl-tn--working gl-tn--${live?.working ? toneFor(live.working.agent) : 'plain'}`} role="status">
            <span className="gl-tn__dot" aria-hidden="true" />
            <p className="gl-tn__working">
              {live?.working ? t.working(shortOf(live.working.agent), live.working.revision) : t.starting}
              <span className="gl-dots" aria-hidden="true" />
            </p>
            {live && (
              <p className="gl-fine">
                {t.calls(Math.min(live.calls + (live.working ? 1 : 0), live.max_calls), live.max_calls)}, {secs(live.elapsed_ms)}
              </p>
            )}
          </li>
        )}
      </ol>
      {run.phase === 'error' && <ErrorBanner error={run.error} onRetry={start} />}

      {finished && outcome && <Outcome outcome={outcome} data={data} t={t} speaking={speaking === 'summary'} inDraft={inDraft} onUse={() => onUse?.(useKey)} onDrop={() => onUse?.(null)} />}

      {data?.how && (
        <details className="gl-details gl-neg__how">
          <summary>{t.how}</summary>
          <p>{data.how}</p>
        </details>
      )}
    </div>
  )
}

function Terms({ items, changed, t }) {
  return (
    <dl className="gl-terms">
      {items.map((d) => (
        <div key={d.k} className={changed?.has(d.k) ? 'is-changed' : undefined}>
          <dt>
            {t.terms[d.k] || d.label}
            {changed?.has(d.k) && <span className="gl-terms__chg"> {t.changed}</span>}
          </dt>
          <dd>{d.text}</dd>
        </div>
      ))}
    </dl>
  )
}

function Turn({ turn, newRound, changed, notes, t, tone, who, speaking }) {
  const v = turn.verdict
  const p = turn.proposal
  const accept = turn.kind === 'accept'
  const hasMore = p.concerns?.length > 0 || p.note || v.findings.length > 1 || notes
  return (
    <li className={`gl-tn gl-tn--${tone}${v.ok ? ' is-ok' : ' is-bad'}${accept ? ' is-accept' : ''}${speaking ? ' is-speaking' : ''}`}>
      <span className="gl-tn__dot" aria-hidden="true" />
      <div className="gl-tn__head">
        <span className="gl-tn__line">
          <strong className="gl-tn__who">{t.agent(who)}</strong> {t.kind[turn.kind] || turn.kind}
        </span>
        <span className="gl-tn__meta">
          {newRound && <span>{t.round(turn.round)}</span>}
          {!turn.cached && turn.ms != null && <span>{secs(turn.ms)}</span>}
          {turn.retried && <span>{t.retried}</span>}
        </span>
      </div>
      {!accept && <Terms items={turn.plain} changed={changed} t={t} />}
      {v.ok ? (
        <p className="gl-verdict is-ok">
          <VerdictIcon ok /> {t.verified}
        </p>
      ) : (
        <p className="gl-verdict is-bad">
          <VerdictIcon />
          <span>
            <strong>{t.rejected}:</strong> {v.findings[0]} <span className="gl-verdict__back">{t.sentBack}</span>
          </span>
        </p>
      )}
      {hasMore && (
        <details className="gl-tn__more">
          <summary>{t.more}</summary>
          {p.note && <p>{p.note}</p>}
          {p.concerns?.length > 0 && (
            <>
              <h5>{t.concerns}</h5>
              <ul>
                {p.concerns.map((c, i) => (
                  <li key={i}>{c}</li>
                ))}
              </ul>
            </>
          )}
          {v.findings.length > 1 && (
            <>
              <h5>{t.findings}</h5>
              <ul>
                {v.findings.map((f, i) => (
                  <li key={i}>{f}</li>
                ))}
              </ul>
            </>
          )}
          {notes && (
            <>
              <h5>{t.notes}</h5>
              <p>{notes}</p>
            </>
          )}
        </details>
      )}
    </li>
  )
}

function VerdictIcon({ ok = false }) {
  return (
    <svg className="gl-verdict__i" viewBox="0 0 16 16" aria-hidden="true">
      {ok ? <path d="M3.5 8.5l3 3 6-7" /> : <path d="M4.5 4.5l7 7M11.5 4.5l-7 7" />}
    </svg>
  )
}

function Outcome({ outcome, data, t, speaking, inDraft, onUse, onDrop }) {
  const o = outcome
  const meta =
    data.by === 'gemini' ? t.by(data.models?.join(' + ') || data.model || 'Gemini', data.calls, data.cached ? t.cached : secs(data.ms)) : t.plainWhy
  if (!o.agreed) {
    return (
      <div className={`gl-result is-none${speaking ? ' is-speaking' : ''}`} role="status">
        <h4>{t.noDeal}</h4>
        <p>{o.reason}</p>
        <p className="gl-fine">{meta}</p>
      </div>
    )
  }
  const terms = o.terms
  return (
    <div className={`gl-result is-agreed${speaking ? ' is-speaking' : ''}`} role="status">
      <div className="gl-result__head">
        <VerdictIcon ok />
        <div>
          <h4>{t.agreed(o.round)}</h4>
          <p className="gl-fine">{t.agreedSub}</p>
        </div>
        <p className="gl-result__save">
          <span className="gl-fine">{t.savings}</span>
          <strong>{fmtRange(terms.savings.low, terms.savings.high, terms.savings.unit)}</strong>
        </p>
      </div>
      <Terms items={o.plain} t={t} />
      {o.next && <p className="gl-result__next">{o.next}</p>}
      <div className="gl-result__actions">
        {inDraft ? (
          <>
            <span className="gl-result__indraft">
              <VerdictIcon ok /> {t.inDraft}
            </span>
            <button type="button" className="gl-link" onClick={onDrop}>
              {t.drop}
            </button>
          </>
        ) : (
          <Button onClick={onUse} disabled={!o.verified}>
            {t.use}
          </Button>
        )}
      </div>
      <p className="gl-fine">
        {o.reason} {meta}.
      </p>
    </div>
  )
}
