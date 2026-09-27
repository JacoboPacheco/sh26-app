import { useMemo, useState } from 'react'
import AiBadge from '../ai/AiBadge'
import Gloss from './Gloss'
import HearNegotiation from './HearNegotiation'
import { planLines } from './hearLines'
import { fmtRange, toneOf, utilityShort } from './format'
import { rangeText } from './plain'

// Step 3 of the guided path, "Agree on a plan": the ways two companies could build together, proposed by one Gemini
// agent per company (each given a goal from its own filing) and compared by a neutral coordinator; the pipeline checks
// every figure against the two filings and drops what they don't support (POST /api/gridlock/plans). 2-3 cards side by
// side, each saying what happens and what each company gains and gives up; the coordinator's pick is marked; the
// agents' steps sit behind "Watch the agents work". Choosing a plan drafts the agreement from its terms (PairSheet).

const T = {
  en: {
    intro: "Each company's AI agent read its own filing and proposed ways to work together. A neutral coordinator compared them, and the pipeline checked every figure against the two filings.",
    goal: (u) => `${u}'s agent was asked to`,
    rec: 'The coordinator recommends this',
    by: (x) => `Proposed by ${x}`,
    when: 'When',
    noWindow: 'Each keeps its filed dates',
    saves: 'Together saves',
    split: 'Who pays the shared cost',
    splitTip: 'Each company pays this share of the one shared cost. What it saves is the rest of what it would have paid alone, so paying a bigger share means saving less.',
    settle: 'How the shared cost is split',
    whyOnly: (n) => `Why only ${n} plan${n === 1 ? '' : 's'}:`,
    gains: 'Gains',
    gives: 'Gives up',
    nothing: 'Nothing',
    noSaving: 'No priced saving',
    months: (n) => `${n} month${n === 1 ? '' : 's'}`,
    outages: (n) => `${n} outage${n === 1 ? '' : 's'} avoided`,
    checked: (n) => `${n} figure${n === 1 ? '' : 's'} checked against the filings`,
    noChecks: 'No figure to check: this plan claims no priced saving',
    dropped: (n) => `${n} dropped`,
    droppedWhy: 'Dropped because the filings and cost sources do not support them:',
    choose: 'Draft the agreement from this plan',
    chosen: 'Drafting from this plan',
    chosenDone: 'The draft below uses this plan',
    watch: (n) => `Watch the agents work (${n} step${n === 1 ? '' : 's'})`,
    working: 'The agents are working',
    workingWhat: [
      (a) => `${a}'s agent reads its own filing and its goal`,
      (b) => `${b}'s agent reads its own filing and its goal`,
      () => 'The coordinator compares their proposals; the pipeline checks every figure',
    ],
    slow: 'Still working: a first run for a pair takes up to half a minute; later visits open at once.',
    failed: "The plans couldn't be prepared.",
    retry: 'Try again',
    template: 'Template plans (AI unavailable)',
    templateTip: 'Gemini was unavailable, too slow or over its daily limit: these plans come from the plain rule-based template, checked the same way',
    verdict: { kept: 'kept', dropped: 'dropped', revised: 'revised' },
    source: 'Source',
  },
  es: {
    intro: 'El agente de IA de cada empresa leyó su propio documento y propuso formas de trabajar juntos. Un coordinador neutral las comparó y el sistema comprobó cada cifra con los dos documentos.',
    goal: (u) => `Al agente de ${u} se le pidió`,
    rec: 'El coordinador recomienda este',
    by: (x) => `Propuesto por ${x}`,
    when: 'Cuándo',
    noWindow: 'Cada una mantiene sus fechas',
    saves: 'Juntos ahorran',
    split: 'Quién paga el costo compartido',
    splitTip: 'Cada empresa paga esta parte del único costo compartido. Lo que ahorra es el resto de lo que habría pagado sola: pagar una parte mayor significa ahorrar menos.',
    settle: 'Cómo se reparte el costo compartido',
    whyOnly: (n) => `Por qué solo ${n} plan${n === 1 ? '' : 'es'}:`,
    gains: 'Gana',
    gives: 'Cede',
    nothing: 'Nada',
    noSaving: 'Sin ahorro cuantificado',
    months: (n) => `${n} mes${n === 1 ? '' : 'es'}`,
    outages: (n) => `${n} corte${n === 1 ? '' : 's'} evitado${n === 1 ? '' : 's'}`,
    checked: (n) => `${n} cifra${n === 1 ? '' : 's'} comprobada${n === 1 ? '' : 's'} con los documentos`,
    noChecks: 'Nada que comprobar: este plan no promete un ahorro cuantificado',
    dropped: (n) => `${n} descartada${n === 1 ? '' : 's'}`,
    droppedWhy: 'Descartadas porque los documentos y las fuentes de costos no las respaldan:',
    choose: 'Redactar el acuerdo con este plan',
    chosen: 'Redactando con este plan',
    chosenDone: 'El borrador de abajo usa este plan',
    watch: (n) => `Ver trabajar a los agentes (${n} paso${n === 1 ? '' : 's'})`,
    working: 'Los agentes están trabajando',
    workingWhat: [
      (a) => `El agente de ${a} lee su propio documento y su objetivo`,
      (b) => `El agente de ${b} lee su propio documento y su objetivo`,
      () => 'El coordinador compara las propuestas; el sistema comprueba cada cifra',
    ],
    slow: 'Sigue trabajando: la primera vez tarda hasta medio minuto; después abre al instante.',
    failed: 'No se pudieron preparar los planes.',
    retry: 'Reintentar',
    template: 'Planes de plantilla (IA no disponible)',
    templateTip: 'Gemini no respondió a tiempo o superó su límite diario: estos planes salen de la plantilla simple, comprobada igual',
    verdict: { kept: 'se mantiene', dropped: 'descartada', revised: 'revisada' },
    source: 'Fuente',
  },
}

const KIND = {
  en: {
    one_outage: 'One shared outage',
    shift: 'Move one schedule',
    share_prep: 'Share the prep work',
    stagger: 'Hand over between jobs',
    status_check: 'Compare notes',
  },
  es: {
    one_outage: 'Un solo corte',
    shift: 'Mover un calendario',
    share_prep: 'Compartir la preparación',
    stagger: 'Relevo entre obras',
    status_check: 'Comparar avances',
  },
}

// the trace's step names, in plain words
const STEP = {
  en: { goal: 'Its goal', settles: 'Settles the split', proposes: 'Proposes', merges: 'Merges the proposals', revises: 'Revises', checks: 'Checks every figure', recommends: 'Recommends', fallback: 'Template' },
  es: { goal: 'Su objetivo', settles: 'Fija el reparto', proposes: 'Propone', merges: 'Une las propuestas', revises: 'Revisa', checks: 'Comprueba cada cifra', recommends: 'Recomienda', fallback: 'Plantilla' },
}

// the colour of the company an agent represents (the coordinator and the pipeline's checks stay neutral)
const agentTone = (name = '') => (/DESC|Dominion/.test(name) ? 'desc' : /Georgia Power/.test(name) ? 'gpc' : /GTC|MEAG|Dalton/.test(name) ? 'ga' : null)

// Gemini sometimes titles a plan in Title Case; the page speaks in sentence case. Words that are names (the two
// projects' and companies' own words, codes, anything with a digit) keep their capitals.
function sentenceCase(title, keep) {
  if (!title) return title
  const words = title.split(' ')
  const long = words.filter((w) => w.length > 3)
  if (long.filter((w) => /^[A-Z][a-z]/.test(w)).length < Math.max(2, long.length * 0.6)) return title
  return words
    .map((w, i) => {
      if (i === 0 || /\d/.test(w) || /^[A-Z&]{2,}\W*$/.test(w) || /^Mc[A-Z]/.test(w)) return w
      const core = w.replace(/[^A-Za-z]/g, '')
      return keep.has(core.toLowerCase()) ? w : w.toLowerCase()
    })
    .join(' ')
}

const words = (s) =>
  String(s || '')
    .toLowerCase()
    .replace(/\b(and|the|a|y|el|la|los|las)\b/g, '')
    .replace(/[^a-zà-ü]/g, '')
const sameWords = (a, b) => !!a && words(a) === words(b)

const usd = (r) => (Array.isArray(r) && r.length === 2 ? fmtRange(r[0], r[1], 'USD') : null)

// the plan's total: the pipeline's figure when sent, else the sum of what each company gains in money
function planSaves(p) {
  if (Array.isArray(p.savings) && p.savings.length === 2) return p.savings
  if (p.savings && (p.savings.low != null || p.savings.high != null)) return [p.savings.low ?? p.savings.high, p.savings.high ?? p.savings.low]
  let lo = 0
  let hi = 0
  let any = false
  for (const inc of p.incentives || []) {
    for (const x of inc.gains || []) {
      if (Array.isArray(x.usd)) {
        lo += x.usd[0]
        hi += x.usd[1]
        any = true
      }
    }
  }
  return any ? [lo, hi] : null
}

export function PlansLoading({ names, lang, ms }) {
  const t = T[lang] || T.en
  const [a, b] = names
  return (
    <div className="bt-plans" aria-busy="true">
      <div className="bt-working" role="status">
        <p className="bt-working__h">
          <span className="bt-working__pulse" aria-hidden="true" />
          {t.working}
        </p>
        <ul>
          {t.workingWhat.map((f, i) => (
            <li key={i}>{f(i === 0 ? a : b)}</li>
          ))}
        </ul>
        {ms > 8000 && <p className="gl-fine">{t.slow}</p>}
        <div className="bt-working__bar" aria-hidden="true">
          <span />
        </div>
      </div>
      <div className="bt-cards">
        {[0, 1, 2].map((i) => (
          <div key={i} className="bt-card bt-card--skel" aria-hidden="true">
            <p className="bt-skel bt-skel--short" />
            <p className="bt-skel bt-skel--title" />
            <p className="bt-skel" />
            <p className="bt-skel" />
            <p className="bt-skel bt-skel--short" />
          </div>
        ))}
      </div>
    </div>
  )
}

export default function PlanCards({ data, lang, chosen, drafting, onChoose }) {
  const t = T[lang] || T.en
  const kinds = KIND[lang] || KIND.en
  const plans = data.plans || []
  const rec = data.recommended?.plan
  const companies = data.companies || []
  const trace = (data.trace || []).map((s, i) => ({ ...s, i })).filter((s) => s && s.text)
  const hear = useMemo(() => planLines(data), [data])
  const [speaking, setSpeaking] = useState(null) // the trace line being read aloud
  const [traceOpen, setTraceOpen] = useState(false)
  // the names on this pair (kept capitalised when a plan title is put in sentence case)
  const keep = new Set(
    companies
      .flatMap((c) => [c.name, c.project?.name, utilityShort(c.utility)])
      .join(' ')
      .split(/[^A-Za-z]+/)
      .filter((w) => w.length > 2 && /^[A-Z]/.test(w))
      .map((w) => w.toLowerCase()),
  )
  const stepNames = STEP[lang] || STEP.en
  // an agent's line starts with the plan kind's id ("stagger: ..."): say the kind in words
  const traceText = (s) => s.text.replace(/^([a-z_]+):\s*/, (m, k) => (kinds[k] ? `${kinds[k]}: ` : m))
  return (
    <div className="bt-plans">
      <div className="bt-plans__intro">
        <p>
          {t.intro}
          {data.how && lang === 'en' && (
            <>
              {' '}
              <Gloss tip={data.how}>How it works</Gloss>
            </>
          )}
        </p>
        {data.fallback ? (
          <span className="aib aib--fallback" title={data.fallback_reason ? `${t.templateTip} (${data.fallback_reason})` : t.templateTip}>
            <span className="aib__dot" aria-hidden="true" />
            {lang === 'en' && data.fallback_label ? data.fallback_label.replace(' - ', ': ') : t.template}
          </span>
        ) : (
          <AiBadge by="gemini" lang={lang}>
            {data.model || null}
          </AiBadge>
        )}
      </div>

      {hear.length > 0 && <HearNegotiation lines={hear} lang={lang} onActive={setSpeaking} />}
      {companies.length > 0 && (
        <ul className="bt-goals" aria-label={lang === 'es' ? 'Objetivos de cada agente' : "Each agent's goal"}>
          {companies.map((c) => (
            <li key={c.utility} className={`bt-goal bt-goal--${toneOf(c.utility)}`}>
              <span className="bt-goal__who">
                <span className={`gl-swatch gl-swatch--${toneOf(c.utility)}`} aria-hidden="true" />
                {t.goal(utilityShort(c.utility))}
              </span>
              <span className="bt-goal__text">{c.goal}</span>
              {c.project?.source_url && (
                <a className="bt-goal__src" href={c.project.source_url} target="_blank" rel="noreferrer">
                  {c.project.source || (c.project.page ? `p. ${c.project.page}` : t.source)}
                </a>
              )}
            </li>
          ))}
        </ul>
      )}

      {data.conflict?.text && (
        <p className={`bt-conflict${data.conflict.split ? '' : ' bt-conflict--fit'}`}>
          <strong>{t.settle}:</strong> {data.conflict.text}
        </p>
      )}

      <ol className={`bt-cards bt-cards--${Math.min(plans.length, 3)}`}>
        {plans.map((p) => {
          const isRec = p.id === rec
          const isOn = chosen === p.id
          const saves = planSaves(p)
          const checks = p.checks || []
          const ok = checks.filter((c) => c.ok).length
          const dropped = [...(p.dropped || []), ...checks.filter((c) => !c.ok).map((c) => ({ claim: c.figure, reason: c.reason }))]
          return (
            <li key={p.id} className={`bt-card${isRec ? ' bt-card--rec' : ''}${isOn ? ' is-on' : ''}`}>
              <div className="bt-card__top">
                {/* the kind's name, unless the title already says it ("Keep dates, share crews" over "Keep dates and share crews") */}
                <span className="bt-card__kind">{sameWords(kinds[p.kind], p.title) ? '' : kinds[p.kind] || p.kind}</span>
                {isRec && <span className="bt-card__rec">{t.rec}</span>}
              </div>
              <h4 className="bt-card__title">{sentenceCase(p.title, keep)}</h4>
              <p className="bt-card__sum">{p.summary}</p>
              <dl className="bt-card__kv">
                <div>
                  <dt>{t.when}</dt>
                  <dd>{p.window?.start ? rangeText(p.window.start, p.window.end, lang) : t.noWindow}</dd>
                </div>
                {saves && (
                  <div>
                    <dt>{t.saves}</dt>
                    <dd className="bt-card__save">{fmtRange(saves[0], saves[1], 'USD')}</dd>
                  </div>
                )}
                {Array.isArray(p.split?.shares) && p.split.shares.length === 2 && (
                  <div title={[p.split.label, p.split.basis].filter(Boolean).join(': ') || undefined}>
                    <dt>{lang === 'es' ? t.split : <Gloss tip={t.splitTip}>{t.split}</Gloss>}</dt>
                    <dd>
                      {p.split.shares.map((s, i) => `${utilityShort((p.incentives || [])[i]?.utility) || (i ? 'B' : 'A')} ${typeof s === 'number' ? s : s.pct} %`).join(', ')}
                    </dd>
                  </div>
                )}
              </dl>
              {p.steps?.length > 0 && (
                <ol className="bt-card__steps">
                  {p.steps.slice(0, 4).map((s) => (
                    <li key={s}>{s}</li>
                  ))}
                </ol>
              )}
              <div className="bt-inc">
                {(p.incentives || []).map((inc) => (
                  <section key={inc.utility} className={`bt-inc__co bt-inc__co--${toneOf(inc.utility)}`} aria-label={utilityShort(inc.utility)}>
                    <h5>
                      <span className={`gl-swatch gl-swatch--${toneOf(inc.utility)}`} aria-hidden="true" />
                      {utilityShort(inc.utility)}
                    </h5>
                    <Terms label={t.gains} list={inc.gains} t={t} sign="+" none={t.noSaving} />
                    <Terms label={t.gives} list={inc.gives_up} t={t} sign="−" none={t.nothing} />
                    {inc.net && <p className="bt-inc__net">{inc.net}</p>}
                  </section>
                ))}
              </div>
              <div className="bt-card__checked">
                <svg viewBox="0 0 16 16" aria-hidden="true">
                  <path d="M3.5 8.5l3 3 6-7" />
                </svg>
                {checks.length ? t.checked(ok) : t.noChecks}
                {dropped.length > 0 && (
                  <details className="bt-card__dropped">
                    <summary>{t.dropped(dropped.length)}</summary>
                    <p className="gl-fine">{t.droppedWhy}</p>
                    <ul>
                      {dropped.map((x, i) => (
                        <li key={i}>
                          <s>{x.claim}</s> <span className="gl-fine">({x.reason})</span>
                        </li>
                      ))}
                    </ul>
                  </details>
                )}
              </div>
              <p className="bt-card__by">{t.by(p.proposed_by || '–')}</p>
              <button type="button" className={`bt-choose${isOn ? ' is-on' : ''}`} aria-pressed={isOn} onClick={() => onChoose(p.id)}>
                {isOn ? (drafting ? t.chosen : t.chosenDone) : t.choose}
              </button>
            </li>
          )
        })}
      </ol>
      {plans.length < 3 && data.not_offered?.length > 0 && (
        <p className="gl-fine bt-notoffered">
          <strong>{t.whyOnly(plans.length)}</strong> {data.not_offered.map((x) => x.why).join(' ')}
        </p>
      )}
      {data.recommended?.why && (
        <p className="bt-recwhy">
          <strong>{t.rec}:</strong> {data.recommended.why}
        </p>
      )}

      {trace.length > 0 && (
        <details className="bt-trace" open={traceOpen || speaking != null} onToggle={(e) => setTraceOpen(e.currentTarget.open)}>
          <summary>{t.watch(trace.length)}</summary>
          {data.how && lang === 'en' && <p className="gl-fine bt-trace__how">{data.how}</p>}
          <ol>
            {trace.map((s) => (
              <li key={s.i} className={`bt-trace__step${s.verdict ? ` is-${s.verdict}` : ''}${speaking === s.i ? ' is-speaking' : ''}`}>
                <span className="bt-trace__who">
                  {agentTone(s.agent) && <span className={`gl-swatch gl-swatch--${agentTone(s.agent)}`} aria-hidden="true" />} {s.agent}
                  {s.step && <span className="bt-trace__step-name">{stepNames[s.step] || s.step}</span>}
                </span>
                <span className="bt-trace__text">{traceText(s)}</span>
                {s.verdict && <span className={`bt-verdict bt-verdict--${s.verdict}`}>{t.verdict[s.verdict] || s.verdict}</span>}
              </li>
            ))}
          </ol>
        </details>
      )}
      {data.disclaimer && <p className="gl-fine">{data.disclaimer}</p>}
    </div>
  )
}

function Terms({ label, list, t, sign, none }) {
  const items = list || []
  return (
    <div className={`bt-terms bt-terms--${sign === '+' ? 'gain' : 'give'}`}>
      <span className="bt-terms__label">{label}</span>
      {items.length ? (
        <ul>
          {items.map((x, i) => {
            const bits = [usd(x.usd), x.months ? t.months(x.months) : null, x.outages_avoided ? t.outages(x.outages_avoided) : null].filter(Boolean)
            return (
              <li key={i} title={x.source ? `${t.source}: ${x.source}` : undefined}>
                <span className="bt-terms__what">{x.what}</span>
                {bits.length > 0 && <span className="bt-terms__fig">{bits.join(', ')}</span>}
              </li>
            )
          })}
        </ul>
      ) : (
        <p className="bt-terms__none">{none}</p>
      )}
    </div>
  )
}
